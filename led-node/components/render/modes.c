#include "modes.h"

#include <stdio.h>
#include <string.h>

#include "esp_check.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/task.h"

#include "anim.h"
#include "canvas.h"
#include "output.h"

static const char *TAG = "modes";

/* 8 ms -> 125 Hz. The strip refresh for 60 RGBW pixels is ~2.4 ms, so this
 * leaves ample headroom, and it keeps the countdown head's sub-pixel motion
 * smooth (one pixel of travel takes 16.7 ms, so ~2 frames per pixel). */
#define FRAME_MS        8
#define RENDER_TASK_PRIO 6
#define RENDER_TASK_CORE 1

static const anim_fn ANIM[MODE_COUNT] = {
    [MODE_BOOT]      = anim_boot,
    [MODE_IDLE]      = anim_idle,
    [MODE_PLAYFUL]   = anim_playful,
    [MODE_READY]     = anim_ready,
    [MODE_COUNTDOWN] = anim_countdown,
    [MODE_CAPTURE]   = anim_capture,
    [MODE_PRINTING]  = anim_printing,
    [MODE_FINISHED]  = anim_finished,
    [MODE_TEST]      = anim_test,
    [MODE_ERROR]     = anim_error,
    [MODE_LINKLOST]  = anim_linklost,
};

/* Owned exclusively by the render task. */
static struct {
    mode_id_t     mode;
    mode_params_t params;
    int64_t       entry_ms;

    mode_id_t     prev;
    mode_params_t prev_params;
    int64_t       prev_entry_ms;

    int64_t       last_rx_ms;
} s;

static QueueHandle_t s_cmd_q;

static inline int64_t now_ms(void)
{
    return esp_timer_get_time() / 1000;
}

static void enter(mode_id_t mode, const mode_params_t *params)
{
    const int64_t t = now_ms();

    s.prev          = s.mode;
    s.prev_params   = s.params;
    s.prev_entry_ms = s.entry_ms;

    s.mode     = mode;
    s.params   = params != NULL ? *params : (mode_params_t){0};
    s.entry_ms = t;
}

/* --- command application -------------------------------------------------- */

static void apply(const command_t *cmd, char *reply, size_t reply_sz)
{
    mode_params_t p = s.params;

    switch (cmd->verb) {
        case CMD_PING:
            /* Normally no mode change: its job is to feed the link watchdog.
             *
             * Boot and Link Lost are the exception. Both states exist only
             * because we had not heard from the host, and a ping is proof that
             * we have. Without this, a host that stalls past the watchdog and
             * then recovers gets healthy PONGs back while the ring sits on the
             * link-lost pattern until the next mode command -- which at an idle
             * booth can be a long time, and which nothing reports, since the
             * node never volunteers its mode over the wire.
             *
             * Mode commands recover on their own by entering a target. This is
             * the path for the heartbeat, which enters nothing. Recovering here
             * rather than in drain_queue() is deliberate: doing it for every
             * inbound line would enter Idle and then immediately enter the
             * command's real target, leaving prev == IDLE, and the cross-fade
             * would blend from an idle frame that was never on screen. */
            if (s.mode == MODE_BOOT || s.mode == MODE_LINKLOST) {
                ESP_LOGI(TAG, "link back");
                enter(MODE_IDLE, NULL);
            }
            snprintf(reply, reply_sz, "PONG");
            return;

        case CMD_IDLE:
        case CMD_RELEASE:
            enter(MODE_IDLE, NULL);
            break;

        case CMD_READY:
            /* No timeout of its own. The two hold-forever modes that have one
             * (Printing, Capture) wait on events that can fail silently; this
             * one is covered twice already — the Pi resets a stalled session on
             * its own watchdog, and if the Pi dies outright the link watchdog
             * takes the ring to Link Lost. */
            enter(MODE_READY, NULL);
            break;

        case CMD_PHASE:
            if (cmd->arg < 0 || cmd->arg > 359) {
                snprintf(reply, reply_sz, "ERR RANGE");
                return;
            }
            p.hue = (float)cmd->arg;
            enter(MODE_PLAYFUL, &p);
            break;

        case CMD_COUNTDOWN:
            if (cmd->arg <= 0 || cmd->arg > 60000) {
                snprintf(reply, reply_sz, "ERR RANGE");
                return;
            }
            p.duration_ms = (uint32_t)cmd->arg;
            enter(MODE_COUNTDOWN, &p);
            break;

        case CMD_CAPTURE:
            enter(MODE_CAPTURE, NULL);
            break;

        case CMD_PRINTING:
            enter(MODE_PRINTING, NULL);
            break;

        case CMD_FINISHED:
            if (cmd->arg <= 0 || cmd->arg > 60000) {
                snprintf(reply, reply_sz, "ERR RANGE");
                return;
            }
            p.duration_ms = (uint32_t)cmd->arg;
            enter(MODE_FINISHED, &p);
            break;

        case CMD_TEST:
            /* Clamps rather than rejects, like ERROR: an out-of-range channel
             * lights all four, which is still a useful answer to "is this pixel
             * alive". No negative check — parse_int is digits-only, so a minus
             * sign never gets this far, and a guard here would be dead code
             * claiming a boundary it does not own. */
            p.code = cmd->arg > TEST_CH_WHITE ? TEST_CH_ALL : cmd->arg;
            enter(MODE_TEST, &p);
            break;

        case CMD_ERROR:
            p.code = cmd->arg;
            enter(MODE_ERROR, &p);
            break;

        default:
            snprintf(reply, reply_sz, "ERR UNKNOWN");
            return;
    }

    snprintf(reply, reply_sz, "OK %s", command_verb_name(cmd->verb));
}

static void drain_queue(void)
{
    cmd_req_t req;

    while (xQueueReceive(s_cmd_q, &req, 0) == pdTRUE) {
        /* Any inbound line feeds the watchdog, including PING. */
        s.last_rx_ms = now_ms();

        cmd_reply_t rep = {0};
        apply(&req.cmd, rep.text, sizeof(rep.text));

        if (req.reply_q != NULL) {
            xQueueSend(req.reply_q, &rep, 0);
        }
    }
}

/* --- timeouts and watchdog ------------------------------------------------ */

static void check_deadlines(int64_t t)
{
    const int64_t elapsed = t - s.entry_ms;

    switch (s.mode) {
        case MODE_PRINTING:
            /* A jammed printer would otherwise leave the ring cheerfully
             * rolling ink forever. */
            if (elapsed > MODE_PRINTING_TIMEOUT_MS) {
                ESP_LOGW(TAG, "printing timed out");
                mode_params_t p = {.code = 0};
                enter(MODE_ERROR, &p);
            }
            break;

        case MODE_CAPTURE:
            /* Full white is the highest-current, highest-heat state in the
             * system. It must never be held indefinitely. */
            if (elapsed > MODE_CAPTURE_TIMEOUT_MS) {
                ESP_LOGW(TAG, "capture never released");
                enter(MODE_IDLE, NULL);
            }
            break;

        case MODE_TEST:
            /* Waits on a human rather than on the booth. Longer than Capture's
             * deadline so there is time to inspect every pixel, but a deadline
             * all the same: TEST 4 is the same load as full white. */
            if (elapsed > MODE_TEST_TIMEOUT_MS) {
                ESP_LOGW(TAG, "test mode timed out");
                enter(MODE_IDLE, NULL);
            }
            break;

        case MODE_FINISHED:
            /* Resolve on its own, so a guest walking away does not leave the
             * ring celebrating at nobody. */
            if (s.params.duration_ms > 0 && elapsed > (int64_t)s.params.duration_ms) {
                enter(MODE_IDLE, NULL);
            }
            break;

        default:
            break;
    }

    /* Link watchdog. Boot has never heard from the host, so it is exempt until
     * the first line arrives. */
    if (s.mode != MODE_BOOT && s.mode != MODE_LINKLOST &&
        (t - s.last_rx_ms) > MODE_LINK_TIMEOUT_MS) {
        ESP_LOGW(TAG, "link lost");
        /* Entering Link Lost from Capture is exactly the safety case this
         * exists for: a dead host must not strand the strip at full white. */
        enter(MODE_LINKLOST, NULL);
    }
}

/* --- render --------------------------------------------------------------- */

static canvas_t s_front;
static canvas_t s_back;

static void render_frame(int64_t t)
{
    const uint32_t elapsed = (uint32_t)(t - s.entry_ms);

    canvas_clear(&s_front);
    ANIM[s.mode](elapsed, &s.params, &s_front);

    /* Cross-fade. Capture is excluded — it owns its ramp, and fading
     * it against a decorative mode would put motion in the key light. */
    if (elapsed < MODE_CROSSFADE_MS && s.mode != MODE_CAPTURE && s.prev != s.mode) {
        canvas_clear(&s_back);
        ANIM[s.prev]((uint32_t)(t - s.prev_entry_ms), &s.prev_params, &s_back);

        const float k = (float)elapsed / (float)MODE_CROSSFADE_MS;
        /* Blending happens in linear light — see canvas.h. */
        canvas_blend(&s_front, &s_back, &s_front, k);
    }

    /* Global brightness is bypassed for Capture (it is photographic equipment,
     * see anim_capture.c) and for Test (a dim die and a dead one look alike at
     * 70%). Everything else is decoration and gets dimmed. */
    output_show(&s_front, s.mode != MODE_CAPTURE && s.mode != MODE_TEST);
}

static void render_task(void *arg)
{
    (void)arg;

    TickType_t last_wake = xTaskGetTickCount();

    while (true) {
        const int64_t t = now_ms();

        drain_queue();
        check_deadlines(t);
        render_frame(t);

        vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(FRAME_MS));
    }
}

static const char *MODE_NAMES[MODE_COUNT] = {
    [MODE_BOOT]      = "BOOT",
    [MODE_IDLE]      = "IDLE",
    [MODE_PLAYFUL]   = "PLAYFUL",
    [MODE_READY]     = "READY",
    [MODE_COUNTDOWN] = "COUNTDOWN",
    [MODE_CAPTURE]   = "CAPTURE",
    [MODE_PRINTING]  = "PRINTING",
    [MODE_FINISHED]  = "FINISHED",
    [MODE_TEST]      = "TEST",
    [MODE_ERROR]     = "ERROR",
    [MODE_LINKLOST]  = "LINKLOST",
};

const char *modes_mode_name(mode_id_t mode)
{
    if (mode < 0 || mode >= MODE_COUNT || MODE_NAMES[mode] == NULL) {
        return "?";
    }
    return MODE_NAMES[mode];
}

void modes_get_state(modes_state_t *out)
{
    const int64_t t = now_ms();

    out->mode        = s.mode;
    out->elapsed_ms  = (uint32_t)(t - s.entry_ms);
    out->since_rx_ms = (uint32_t)(t - s.last_rx_ms);
    out->hue         = s.params.hue;
    out->duration_ms = s.params.duration_ms;
    out->code        = s.params.code;
}

esp_err_t modes_start(QueueHandle_t cmd_q)
{
    s_cmd_q = cmd_q;

    ESP_RETURN_ON_ERROR(output_init(), TAG, "output_init failed");

    const int64_t t = now_ms();
    s.mode          = MODE_BOOT;
    s.prev          = MODE_BOOT;
    s.entry_ms      = t;
    s.prev_entry_ms = t;
    s.last_rx_ms    = t;

    BaseType_t ok = xTaskCreatePinnedToCore(render_task, "render", 4096, NULL,
                                            RENDER_TASK_PRIO, NULL, RENDER_TASK_CORE);
    if (ok != pdPASS) {
        return ESP_ERR_NO_MEM;
    }

    ESP_LOGI(TAG, "render task started at %d Hz", 1000 / FRAME_MS);
    return ESP_OK;
}

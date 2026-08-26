/* The mode state machine: apply() and check_deadlines().
 *
 * These are `static`, so this file #includes modes.c rather than linking it —
 * the standard way to reach internal linkage in C, and the reason the mode
 * logic is testable at all without inventing an accessor nobody needs.
 *
 * Every deadline is derived from esp_timer_get_time(), so the fake clock in
 * stubs/esp_timer.h turns "wait 120 seconds for the printing timeout" into an
 * assignment. The rendering path is stubbed out below: this is a test of the
 * transition logic, not of what the ring looks like.
 *
 * The first two tests are the regression for 14a6f00, where a recovered host
 * got healthy PONGs back while the ring stayed on the link-lost pattern.
 */
#include <stdbool.h>
#include <stdint.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "esp_err.h"
#include "test.h"

/* --- fakes the mode logic needs to link against ------------------------- */

static int64_t s_now_us;

int64_t esp_timer_get_time(void) { return s_now_us; }
void test_clock_set_ms(int64_t ms) { s_now_us = ms * 1000; }
void test_clock_advance_ms(int64_t ms) { s_now_us += ms * 1000; }

/* Rendering is not under test. Stubs keep modes.c linkable without dragging in
 * canvas.c, output.c and nine animations. */
#include "canvas.h"
void canvas_clear(canvas_t *c) { (void)c; }
void canvas_blend(canvas_t *d, const canvas_t *a, const canvas_t *b, float t)
{ (void)d; (void)a; (void)b; (void)t; }

#include "modes.h"
typedef void (*anim_stub_fn)(uint32_t, const mode_params_t *, canvas_t *);
static void anim_noop(uint32_t t, const mode_params_t *p, canvas_t *c)
{ (void)t; (void)p; (void)c; }

void anim_boot(uint32_t t, const mode_params_t *p, canvas_t *c)     { anim_noop(t, p, c); }
void anim_idle(uint32_t t, const mode_params_t *p, canvas_t *c)     { anim_noop(t, p, c); }
void anim_playful(uint32_t t, const mode_params_t *p, canvas_t *c)  { anim_noop(t, p, c); }
void anim_ready(uint32_t t, const mode_params_t *p, canvas_t *c)    { anim_noop(t, p, c); }
void anim_countdown(uint32_t t, const mode_params_t *p, canvas_t *c){ anim_noop(t, p, c); }
void anim_capture(uint32_t t, const mode_params_t *p, canvas_t *c)  { anim_noop(t, p, c); }
void anim_printing(uint32_t t, const mode_params_t *p, canvas_t *c) { anim_noop(t, p, c); }
void anim_finished(uint32_t t, const mode_params_t *p, canvas_t *c) { anim_noop(t, p, c); }
void anim_error(uint32_t t, const mode_params_t *p, canvas_t *c)    { anim_noop(t, p, c); }
void anim_linklost(uint32_t t, const mode_params_t *p, canvas_t *c) { anim_noop(t, p, c); }

esp_err_t output_init(void) { return ESP_OK; }
void output_show(const canvas_t *c, bool apply_brightness)
{ (void)c; (void)apply_brightness; }

QueueHandle_t xQueueCreate(uint32_t l, uint32_t s) { (void)l; (void)s; return (void *)1; }
BaseType_t xQueueSend(QueueHandle_t q, const void *i, TickType_t w)
{ (void)q; (void)i; (void)w; return pdTRUE; }
BaseType_t xQueueReceive(QueueHandle_t q, void *o, TickType_t w)
{ (void)q; (void)o; (void)w; return pdFALSE; }   /* queue always empty */
BaseType_t xTaskCreatePinnedToCore(void (*f)(void *), const char *n, uint32_t st,
                                   void *a, uint32_t p, TaskHandle_t *o, int c)
{ (void)f; (void)n; (void)st; (void)a; (void)p; (void)o; (void)c; return pdPASS; }
TickType_t xTaskGetTickCount(void) { return 0; }
void vTaskDelayUntil(TickType_t *l, TickType_t p) { (void)l; (void)p; }

#include "modes.c"

/* --- helpers ------------------------------------------------------------ */

static char reply[CMD_REPLY_MAX];

static void enter_mode(mode_id_t mode)
{
    test_clock_set_ms(100000);
    s.mode = mode;
    s.prev = mode;
    s.entry_ms = now_ms();
    s.prev_entry_ms = s.entry_ms;
    s.last_rx_ms = now_ms();
    s.params = (mode_params_t){0};
}

/* Advance the clock the way a live booth does: the Pi pings every ~2 s, so
 * last_rx_ms keeps up and the watchdog stays quiet.
 *
 * This is not a convenience. The watchdog (10 s) is SHORTER than the capture
 * (30 s) and printing (120 s) timeouts, so those two are only reachable on a
 * live link — with a silent host the watchdog gets there first, which is the
 * documented safety case and is asserted separately below. Advancing the clock
 * without feeding it tests the watchdog, not the timeout you meant to test.
 */
static void advance_alive_ms(int64_t ms)
{
    const int64_t step = 2000;
    for (int64_t done = 0; done < ms; ) {
        const int64_t chunk = (ms - done) < step ? (ms - done) : step;
        test_clock_advance_ms(chunk);
        s.last_rx_ms = now_ms();   /* a PING landed */
        check_deadlines(now_ms());
        done += chunk;
    }
}

static const char *send(const char *line)
{
    command_t cmd;
    if (!command_parse(line, strlen(line), &cmd)) {
        return "ERR UNKNOWN";
    }
    /* Mirrors drain_queue: any inbound line feeds the watchdog. */
    s.last_rx_ms = now_ms();
    apply(&cmd, reply, sizeof(reply));
    return reply;
}

/* --- the 14a6f00 regression --------------------------------------------- */

TEST(ping_recovers_from_link_lost)
{
    /* Before the fix this returned PONG and left the mode at LINKLOST, so a
     * recovered host saw a healthy link while the ring showed an error. */
    enter_mode(MODE_LINKLOST);
    CHECK_STR(send("PING"), "PONG");
    CHECK_INT(s.mode, MODE_IDLE);
}

TEST(ping_recovers_from_boot)
{
    /* Same shape: a node powered on while the Pi is already up and idle-pinging
     * would otherwise sit in the boot pattern indefinitely. */
    enter_mode(MODE_BOOT);
    CHECK_STR(send("PING"), "PONG");
    CHECK_INT(s.mode, MODE_IDLE);
}

TEST(ping_does_not_disturb_a_healthy_mode)
{
    /* Recovery must be limited to the two states that exist because we had not
     * heard from the host. A PING during a countdown must not reset the ring. */
    enter_mode(MODE_COUNTDOWN);
    CHECK_STR(send("PING"), "PONG");
    CHECK_INT(s.mode, MODE_COUNTDOWN);

    enter_mode(MODE_CAPTURE);
    send("PING");
    CHECK_INT(s.mode, MODE_CAPTURE);
}

/* --- command application ------------------------------------------------ */

TEST(commands_enter_their_documented_modes)
{
    enter_mode(MODE_IDLE);
    CHECK_STR(send("PHASE 280"), "OK PHASE");
    CHECK_INT(s.mode, MODE_PLAYFUL);
    CHECK_NEAR(s.params.hue, 280.0, 0.01);

    CHECK_STR(send("READY"), "OK READY");
    CHECK_INT(s.mode, MODE_READY);

    CHECK_STR(send("COUNTDOWN 3000"), "OK COUNTDOWN");
    CHECK_INT(s.mode, MODE_COUNTDOWN);
    CHECK_INT(s.params.duration_ms, 3000);

    CHECK_STR(send("CAPTURE"), "OK CAPTURE");
    CHECK_INT(s.mode, MODE_CAPTURE);

    CHECK_STR(send("PRINTING"), "OK PRINTING");
    CHECK_INT(s.mode, MODE_PRINTING);

    CHECK_STR(send("FINISHED 4000"), "OK FINISHED");
    CHECK_INT(s.mode, MODE_FINISHED);

    CHECK_STR(send("ERROR 3"), "OK ERROR");
    CHECK_INT(s.mode, MODE_ERROR);
    CHECK_INT(s.params.code, 3);
}

TEST(release_is_an_exact_alias_of_idle)
{
    enter_mode(MODE_CAPTURE);
    CHECK_STR(send("RELEASE"), "OK RELEASE");
    CHECK_INT(s.mode, MODE_IDLE);
}

TEST(out_of_range_arguments_leave_the_mode_alone)
{
    enter_mode(MODE_PLAYFUL);
    CHECK_STR(send("PHASE 360"), "ERR RANGE");
    CHECK_INT(s.mode, MODE_PLAYFUL);

    CHECK_STR(send("COUNTDOWN 0"), "ERR RANGE");
    CHECK_STR(send("COUNTDOWN 60001"), "ERR RANGE");
    CHECK_STR(send("FINISHED 60001"), "ERR RANGE");
    CHECK_INT(s.mode, MODE_PLAYFUL);
}

TEST(boundary_arguments_are_accepted)
{
    enter_mode(MODE_IDLE);
    CHECK_STR(send("PHASE 0"), "OK PHASE");
    CHECK_STR(send("PHASE 359"), "OK PHASE");
    CHECK_STR(send("COUNTDOWN 1"), "OK COUNTDOWN");
    CHECK_STR(send("COUNTDOWN 60000"), "OK COUNTDOWN");
}

TEST(every_command_is_legal_in_every_mode)
{
    /* The node is a pure sink: the Pi owns sequencing, and a node
     * second-guessing it could only ever disagree with the booth. */
    enter_mode(MODE_PRINTING);
    CHECK_STR(send("COUNTDOWN 3000"), "OK COUNTDOWN");
    CHECK_INT(s.mode, MODE_COUNTDOWN);
}

TEST(ready_holds_until_the_count_actually_starts)
{
    /* The gap this mode exists for is the camera warming up, which the browser
     * owns and neither the node nor the Pi can predict. So Ready must have no
     * deadline of its own: a node that timed out here would drop the ring to
     * Idle in the middle of a session that is proceeding normally. */
    enter_mode(MODE_READY);
    advance_alive_ms(300000);   /* five minutes, host healthy */
    CHECK_INT(s.mode, MODE_READY);

    CHECK_STR(send("COUNTDOWN 3000"), "OK COUNTDOWN");
    CHECK_INT(s.mode, MODE_COUNTDOWN);
    CHECK_INT(now_ms() - s.entry_ms, 0);   /* the sweep starts from zero */
}

TEST(reentering_a_mode_restarts_its_clock)
{
    enter_mode(MODE_IDLE);
    send("COUNTDOWN 3000");
    test_clock_advance_ms(2000);
    send("COUNTDOWN 3000");
    CHECK_INT(now_ms() - s.entry_ms, 0);
}

/* --- deadlines ---------------------------------------------------------- */

TEST(capture_releases_itself_after_thirty_seconds)
{
    /* Full white is the highest-current, highest-heat state in the system, so a
     * host that goes quiet mid-shot must not be able to leave it there. This is
     * the LIVE-link path: the Pi is still pinging but never sent RELEASE. */
    enter_mode(MODE_CAPTURE);
    advance_alive_ms(MODE_CAPTURE_TIMEOUT_MS - 2000);
    CHECK_INT(s.mode, MODE_CAPTURE);

    advance_alive_ms(4000);
    CHECK_INT(s.mode, MODE_IDLE);
}

TEST(printing_fails_to_error_after_two_minutes)
{
    /* A jammed printer would otherwise leave the ring cheerfully rolling ink
     * forever. Live link, for the same reason as the capture timeout. */
    enter_mode(MODE_PRINTING);
    advance_alive_ms(MODE_PRINTING_TIMEOUT_MS - 2000);
    CHECK_INT(s.mode, MODE_PRINTING);

    advance_alive_ms(4000);
    CHECK_INT(s.mode, MODE_ERROR);
    CHECK_INT(s.params.code, 0);
}

TEST(the_watchdog_outruns_the_longer_timeouts_when_the_host_is_silent)
{
    /* The ordering that made the two tests above need a live link, asserted
     * directly: with nothing arriving, Link Lost is reached long before either
     * mode's own timeout, and a dead host therefore never waits 30 s or 120 s
     * for the ring to stop. */
    CHECK(MODE_LINK_TIMEOUT_MS < MODE_CAPTURE_TIMEOUT_MS);
    CHECK(MODE_LINK_TIMEOUT_MS < MODE_PRINTING_TIMEOUT_MS);

    enter_mode(MODE_PRINTING);
    test_clock_advance_ms(MODE_LINK_TIMEOUT_MS + 1);
    check_deadlines(now_ms());
    CHECK_INT(s.mode, MODE_LINKLOST);
}

TEST(finished_returns_to_idle_after_its_duration)
{
    enter_mode(MODE_FINISHED);
    s.params.duration_ms = 4000;
    test_clock_advance_ms(3999);
    check_deadlines(now_ms());
    CHECK_INT(s.mode, MODE_FINISHED);

    test_clock_advance_ms(2);
    check_deadlines(now_ms());
    CHECK_INT(s.mode, MODE_IDLE);
}

/* --- the link watchdog -------------------------------------------------- */

TEST(silence_trips_the_watchdog)
{
    enter_mode(MODE_IDLE);
    test_clock_advance_ms(MODE_LINK_TIMEOUT_MS - 1);
    check_deadlines(now_ms());
    CHECK_INT(s.mode, MODE_IDLE);

    test_clock_advance_ms(2);
    check_deadlines(now_ms());
    CHECK_INT(s.mode, MODE_LINKLOST);
}

TEST(any_inbound_line_holds_the_watchdog_off)
{
    /* The watchdog measures time since any line, not since the last mode
     * change — an idle booth runs for hours without a transition. */
    enter_mode(MODE_IDLE);
    for (int i = 0; i < 10; i++) {
        test_clock_advance_ms(MODE_LINK_TIMEOUT_MS - 1000);
        send("PING");
        check_deadlines(now_ms());
    }
    CHECK_INT(s.mode, MODE_IDLE);
}

TEST(boot_is_exempt_from_the_watchdog)
{
    /* Boot has never heard from the host, so silence is its normal condition. */
    enter_mode(MODE_BOOT);
    test_clock_advance_ms(MODE_LINK_TIMEOUT_MS * 10);
    check_deadlines(now_ms());
    CHECK_INT(s.mode, MODE_BOOT);
}

TEST(link_lost_does_not_re_enter_itself)
{
    enter_mode(MODE_LINKLOST);
    s.entry_ms = now_ms();
    test_clock_advance_ms(MODE_LINK_TIMEOUT_MS * 3);
    check_deadlines(now_ms());
    CHECK_INT(s.mode, MODE_LINKLOST);
    CHECK_INT(now_ms() - s.entry_ms, MODE_LINK_TIMEOUT_MS * 3);
}

TEST(a_dead_host_never_strands_the_strip_at_full_white)
{
    /* The safety case the watchdog exists for: Capture -> Link Lost. */
    enter_mode(MODE_CAPTURE);
    test_clock_advance_ms(MODE_LINK_TIMEOUT_MS + 1);
    check_deadlines(now_ms());
    CHECK_INT(s.mode, MODE_LINKLOST);
}

int main(void)
{
    RUN(ping_recovers_from_link_lost);
    RUN(ping_recovers_from_boot);
    RUN(ping_does_not_disturb_a_healthy_mode);
    RUN(commands_enter_their_documented_modes);
    RUN(release_is_an_exact_alias_of_idle);
    RUN(out_of_range_arguments_leave_the_mode_alone);
    RUN(boundary_arguments_are_accepted);
    RUN(every_command_is_legal_in_every_mode);
    RUN(ready_holds_until_the_count_actually_starts);
    RUN(reentering_a_mode_restarts_its_clock);
    RUN(capture_releases_itself_after_thirty_seconds);
    RUN(printing_fails_to_error_after_two_minutes);
    RUN(the_watchdog_outruns_the_longer_timeouts_when_the_host_is_silent);
    RUN(finished_returns_to_idle_after_its_duration);
    RUN(silence_trips_the_watchdog);
    RUN(any_inbound_line_holds_the_watchdog_off);
    RUN(boot_is_exempt_from_the_watchdog);
    RUN(link_lost_does_not_re_enter_itself);
    RUN(a_dead_host_never_strands_the_strip_at_full_white);
    return test_report("modes");
}

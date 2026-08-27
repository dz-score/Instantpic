/* modes.h — the mode state machine and the render task.
 *
 * The render task is the SOLE owner of mode state. Transports produce commands
 * onto a queue and never touch it, which is why there is no mutex anywhere in
 * this firmware.
 */
#pragma once

#include <stdint.h>

#include "esp_err.h"

#include "command.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    MODE_BOOT = 0,   /* alive, no host yet — also the wiring self-test */
    MODE_IDLE,
    MODE_PLAYFUL,
    MODE_READY,      /* poised, waiting for the count to start */
    MODE_COUNTDOWN,
    MODE_CAPTURE,
    MODE_PRINTING,
    MODE_FINISHED,
    MODE_TEST,       /* bench instrument: one die at a time */
    MODE_ERROR,      /* the host reporting a fault */
    MODE_LINKLOST,   /* the node noticing the host went silent */
    MODE_COUNT,
} mode_id_t;

typedef struct {
    float    hue;          /* PHASE */
    uint32_t duration_ms;  /* COUNTDOWN, FINISHED */
    int32_t  code;         /* ERROR */
} mode_params_t;

/* Cross-fade between modes. Capture is excluded — it has its own 100 ms ramp,
 * and a fade *out* of full white is handled by the incoming mode's fade in. */
#define MODE_CROSSFADE_MS 200

/* Watchdog. Measured from any inbound line, not from the last mode change:
 * Idle runs for hours without a transition, and a change-based watchdog would
 * trip into Link Lost at a perfectly healthy booth.
 *
 * Entry and recovery are symmetric, and both are the heartbeat's job: silence
 * past this timeout enters Link Lost, and the next PING leaves it. An earlier
 * version only had the entry half, so a recovered host got PONGs while the ring
 * stayed dark-patterned -- the watchdog protected against a false alarm on the
 * way in and then stranded the node on the way out. */
#define MODE_LINK_TIMEOUT_MS 10000

/* Hold-forever modes both wait on external events that can fail silently. */
#define MODE_PRINTING_TIMEOUT_MS 120000
#define MODE_CAPTURE_TIMEOUT_MS  30000

/* Test waits on a human, so it is longer than Capture's -- long enough to walk
 * a 60 px ring and look at every pixel. It still has a deadline, because
 * TEST 4 draws the same current as Capture and an operator who wanders off
 * must not leave the strip there. */
#define MODE_TEST_TIMEOUT_MS 120000

/* TEST argument. Anything outside 1..4 lights all four dies at once. */
typedef enum {
    TEST_CH_ALL = 0,
    TEST_CH_RED,
    TEST_CH_GREEN,
    TEST_CH_BLUE,
    TEST_CH_WHITE,
} test_channel_t;

/* Commands are rare (a heartbeat every 2 s plus transitions), and the render
 * task drains the queue every frame, so this only ever buffers a burst. */
#define MODE_CMD_QUEUE_DEPTH 8

/* Starts the render task. Takes ownership of nothing; reads `cmd_q` forever. */
esp_err_t modes_start(QueueHandle_t cmd_q);

/* --- introspection -------------------------------------------------------- */

typedef struct {
    mode_id_t mode;
    uint32_t  elapsed_ms;      /* since entering the current mode */
    uint32_t  since_rx_ms;     /* since the last inbound line — what the watchdog watches */
    float     hue;
    uint32_t  duration_ms;
    int32_t   code;
} modes_state_t;

/* A read-only look at the render task's state, for diagnostics.
 *
 * This does not weaken the "transports never touch mode state" rule: that rule
 * exists so nothing outside the render task can MUTATE state, which is what
 * would demand a lock. Reading a few scalars for a status page creates no such
 * hazard, and a torn read is harmless here. Mutation still only ever happens by
 * putting a command on the queue. */
void modes_get_state(modes_state_t *out);

const char *modes_mode_name(mode_id_t mode);

#ifdef __cplusplus
}
#endif

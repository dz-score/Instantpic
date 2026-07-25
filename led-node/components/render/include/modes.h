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
    MODE_COUNTDOWN,
    MODE_CAPTURE,
    MODE_PRINTING,
    MODE_FINISHED,
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
 * trip into Link Lost at a perfectly healthy booth. */
#define MODE_LINK_TIMEOUT_MS 10000

/* Hold-forever modes both wait on external events that can fail silently. */
#define MODE_PRINTING_TIMEOUT_MS 120000
#define MODE_CAPTURE_TIMEOUT_MS  30000

/* Commands are rare (a heartbeat every 2 s plus transitions), and the render
 * task drains the queue every frame, so this only ever buffers a burst. */
#define MODE_CMD_QUEUE_DEPTH 8

/* Starts the render task. Takes ownership of nothing; reads `cmd_q` forever. */
esp_err_t modes_start(QueueHandle_t cmd_q);

#ifdef __cplusplus
}
#endif

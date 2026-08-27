/* transport.h — command intake.
 *
 * Exactly one implementation is compiled in, chosen by Kconfig:
 *   HTTP  (development) — leaves the USB port free for flashing
 *   UART  (booth)       — the wire the Pi actually uses
 *
 * Both do the same three things: parse a line with the shared parser, enqueue
 * it, wait for the reply. Neither ever touches mode state. That is what makes
 * the swap behavior-identical.
 */
#pragma once

#include "esp_err.h"

#include "command.h"

#ifdef __cplusplus
extern "C" {
#endif

/* How long a transport waits for the render task to apply a command and
 * answer. The render task drains the queue every frame (~8 ms), so this is an
 * error detector, not an expected wait. */
#define TRANSPORT_REPLY_TIMEOUT_MS 200

esp_err_t transport_start(QueueHandle_t cmd_q);
void      transport_stop(void);

/* Shared by both transports: parse, enqueue, await reply. Always writes a
 * NUL-terminated reply, including on parse failure and timeout. */
void transport_submit(QueueHandle_t cmd_q, const char *line, size_t len,
                      char *reply, size_t reply_sz);

/* Creates the reply queue. Called by whichever transport is compiled in. */
esp_err_t transport_common_init(void);

#ifdef __cplusplus
}
#endif

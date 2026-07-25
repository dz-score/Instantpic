/* anim.h — one render function per mode.
 *
 * Every animation is a PURE function of elapsed time. No internal state, no
 * accumulators. This is not stylistic:
 *
 *   - The 200 ms cross-fade renders the outgoing AND incoming mode within the
 *     same frame, which is only possible if a mode can be evaluated at an
 *     arbitrary t without having been "running".
 *   - It eliminates drift.
 *   - It makes every animation unit-testable on the host by evaluating it at a
 *     timestamp and asserting on the canvas.
 *
 * Finished's sparkles need randomness; they derive it deterministically from t
 * so the function stays pure.
 */
#pragma once

#include <stdint.h>

#include "canvas.h"
#include "modes.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef void (*anim_fn)(uint32_t elapsed_ms, const mode_params_t *p, canvas_t *c);

void anim_boot(uint32_t elapsed_ms, const mode_params_t *p, canvas_t *c);
void anim_idle(uint32_t elapsed_ms, const mode_params_t *p, canvas_t *c);
void anim_playful(uint32_t elapsed_ms, const mode_params_t *p, canvas_t *c);
void anim_countdown(uint32_t elapsed_ms, const mode_params_t *p, canvas_t *c);
void anim_capture(uint32_t elapsed_ms, const mode_params_t *p, canvas_t *c);
void anim_printing(uint32_t elapsed_ms, const mode_params_t *p, canvas_t *c);
void anim_finished(uint32_t elapsed_ms, const mode_params_t *p, canvas_t *c);
void anim_error(uint32_t elapsed_ms, const mode_params_t *p, canvas_t *c);
void anim_linklost(uint32_t elapsed_ms, const mode_params_t *p, canvas_t *c);

#ifdef __cplusplus
}
#endif

/* anim.h — one render function per mode.
 *
 * Every animation is a PURE function of elapsed time: no internal state, no
 * accumulators. Docs/LED_NODE_ARCHITECTURE.md §1 has what depends on that.
 *
 * Finished's sparkles need randomness; they derive it deterministically from t
 * so the function stays pure. Anything else needing randomness must do the same.
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
void anim_ready(uint32_t elapsed_ms, const mode_params_t *p, canvas_t *c);
void anim_test(uint32_t elapsed_ms, const mode_params_t *p, canvas_t *c);
void anim_countdown(uint32_t elapsed_ms, const mode_params_t *p, canvas_t *c);
void anim_capture(uint32_t elapsed_ms, const mode_params_t *p, canvas_t *c);
void anim_printing(uint32_t elapsed_ms, const mode_params_t *p, canvas_t *c);
void anim_finished(uint32_t elapsed_ms, const mode_params_t *p, canvas_t *c);
void anim_error(uint32_t elapsed_ms, const mode_params_t *p, canvas_t *c);
void anim_linklost(uint32_t elapsed_ms, const mode_params_t *p, canvas_t *c);

#ifdef __cplusplus
}
#endif

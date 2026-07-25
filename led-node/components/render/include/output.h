/* output.h — the only place that knows about physical pixels.
 *
 * Applies, in order: global brightness (linear), ring geometry
 * (RING_OFFSET / direction), then gamma encoding. Gamma last is not
 * negotiable — everything upstream composites in linear light.
 */
#pragma once

#include <stdbool.h>

#include "esp_err.h"

#include "canvas.h"

#ifdef __cplusplus
extern "C" {
#endif

esp_err_t output_init(void);

/* 0..1. Applied to every mode except Capture. */
void  output_set_brightness(float level);
float output_get_brightness(void);

/* Push a frame. `apply_brightness` is false for Capture, which must run at full
 * scale: SK6812 dimming is PWM in the high hundreds of Hz, and at 1/200s or
 * faster that samples a fraction of a cycle and bands the photo. */
void output_show(const canvas_t *c, bool apply_brightness);

#ifdef __cplusplus
}
#endif

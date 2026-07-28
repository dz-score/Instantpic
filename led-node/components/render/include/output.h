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

/* --- introspection -------------------------------------------------------- */

/* Copies the last frame pushed to the strip as RGBW quads, in PHYSICAL pixel
 * order — exactly the bytes the strip received, after brightness, geometry and
 * gamma. Returns the number of pixels written.
 *
 * Read from another task without locking. A torn frame is possible and
 * harmless: this exists to look at, not to act on. */
size_t output_snapshot(uint8_t *dst, size_t max_pixels);

uint32_t output_frame_count(void);

#ifdef __cplusplus
}
#endif

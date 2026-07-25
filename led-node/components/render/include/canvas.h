/* canvas.h — the linear framebuffer and its drawing primitives.
 *
 * Two rules are enforced here rather than in the animations:
 *
 *  1. Sub-pixel rendering. Every primitive anti-aliases across pixel
 *     boundaries, so no animation *can* forget to. At 60 px and one revolution
 *     per second the countdown head moves one pixel per 16.7 ms; snapping to
 *     whole pixels is plainly visible.
 *
 *  2. Geometry. Animations address the ring in degrees and never see a pixel
 *     index, so RING_OFFSET and direction are applied in exactly one place
 *     (output.c) and the physical seam can be mounted anywhere.
 *
 * Values are LINEAR light, not display-encoded. Compositing and cross-fading
 * must happen in linear space; gamma is applied once at output. Blending
 * gamma-encoded values makes a fade visibly duck dark through its midpoint.
 */
#pragma once

#include "sdkconfig.h"

#ifdef __cplusplus
extern "C" {
#endif

#define RING_LEDS      CONFIG_LED_NODE_RING_LEDS
#define DEG_PER_PIXEL  (360.0f / (float)RING_LEDS)

typedef struct {
    float r, g, b, w;   /* linear, nominally 0..1, may exceed while compositing */
} rgbw_t;

typedef struct {
    rgbw_t px[RING_LEDS];
} canvas_t;

/* --- colors --------------------------------------------------------------- */

rgbw_t rgbw_make(float r, float g, float b, float w);
rgbw_t rgbw_scale(rgbw_t c, float k);
rgbw_t rgbw_add(rgbw_t a, rgbw_t b);
rgbw_t rgbw_lerp(rgbw_t a, rgbw_t b, float t);

/* Hue in degrees, saturation and value 0..1. Returns LINEAR light: the HSV
 * result is display-referred, so it is linearized here to keep the canvas
 * consistent. W is left at zero. */
rgbw_t rgbw_hue(float hue_deg, float sat, float val);

/* The white channel alone. This is what Capture uses — mixing R+G+B to white
 * gives a three-spike spectrum that renders skin blotchy. */
rgbw_t rgbw_white(float level);

/* --- canvas --------------------------------------------------------------- */

void canvas_clear(canvas_t *c);
void canvas_fill(canvas_t *c, rgbw_t color);
void canvas_scale(canvas_t *c, float k);

/* dst = a*(1-t) + b*t, in linear space. */
void canvas_blend(canvas_t *dst, const canvas_t *a, const canvas_t *b, float t);

/* An anti-aliased point centred on `deg`, fading to nothing at `falloff_deg`.
 * Additive. A falloff near DEG_PER_PIXEL gives a crisp head that straddles two
 * pixels. */
void canvas_add_point(canvas_t *c, float deg, rgbw_t color, float falloff_deg);

/* An anti-aliased arc from `deg_start` spanning `deg_span` degrees clockwise.
 * Both ends are anti-aliased by pixel coverage. Additive. */
void canvas_add_arc(canvas_t *c, float deg_start, float deg_span, rgbw_t color);

/* Evaluate a continuous field over the ring. The callback receives the pixel's
 * ANGLE, never its index — smooth full-ring washes have no edges to alias, but
 * they still must not be written in terms of pixel numbers. Additive. */
typedef rgbw_t (*canvas_field_fn)(float deg, void *ctx);
void canvas_add_field(canvas_t *c, canvas_field_fn fn, void *ctx);

/* Smallest absolute angle between two bearings, 0..180. */
float canvas_angular_distance(float a, float b);

/* Wrap into [0, 360). */
float canvas_wrap_deg(float deg);

#ifdef __cplusplus
}
#endif

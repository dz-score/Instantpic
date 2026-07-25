/* Printing — "The CMYK Ink Roller".
 *
 * A "working, please wait" indicator for an unknown duration. The mode manager
 * enforces the 120 s timeout; a jammed printer must not leave the ring
 * cheerfully rolling ink forever.
 *
 * The off-gap is what makes the rotation legible — a continuous colour wheel at
 * this speed reads as a blur. Steady speed and constant direction; resist
 * making it accelerate.
 */
#include "anim.h"

#define ROTATE_MS 2000
#define BAND_DEG  90.0f
#define BAND_VAL  0.45f

void anim_printing(uint32_t t, const mode_params_t *p, canvas_t *c)
{
    (void)p;

    const float rot = (float)(t % ROTATE_MS) / (float)ROTATE_MS * 360.0f;

    /* Cyan, magenta, yellow, then a genuine gap of off pixels. */
    canvas_add_arc(c, rot + 0.0f,   BAND_DEG, rgbw_hue(180.0f, 1.0f, BAND_VAL));
    canvas_add_arc(c, rot + 90.0f,  BAND_DEG, rgbw_hue(300.0f, 1.0f, BAND_VAL));
    canvas_add_arc(c, rot + 180.0f, BAND_DEG, rgbw_hue(60.0f,  1.0f, BAND_VAL));
}

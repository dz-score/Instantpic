/* Printing — "The CMYK Ink Roller".
 *
 * A "working, please wait" indicator for an unknown duration. The mode manager
 * enforces the 120 s timeout; a jammed printer must not leave the ring
 * cheerfully rolling ink forever.
 *
 * The gap between the last band and the first is what makes the rotation
 * legible — a continuous colour wheel at this speed reads as a blur. It is a
 * dim wash rather than genuine off: a quarter of the ring going black looks
 * like a dead segment, not like a design, and that is exactly how it read on
 * the real strip. Contrast still carries the motion (BAND_VAL is 7x the wash),
 * which is the same trade anim_countdown makes with its BACKGROUND.
 *
 * Steady speed and constant direction; resist making it accelerate.
 */
#include "anim.h"

#define ROTATE_MS 2000
#define BAND_DEG  90.0f
#define BAND_VAL  0.45f
#define WASH_VAL  0.06f

void anim_printing(uint32_t t, const mode_params_t *p, canvas_t *c)
{
    (void)p;

    const float rot = (float)(t % ROTATE_MS) / (float)ROTATE_MS * 360.0f;

    /* The floor the whole ring sits on, so the fourth quadrant reads as unlit
     * rather than as broken. W-tinted, so it does not tug the CMY hues that
     * are added on top of it. */
    canvas_fill(c, rgbw_white(WASH_VAL));

    /* Cyan, magenta, yellow, then the gap. */
    canvas_add_arc(c, rot + 0.0f,   BAND_DEG, rgbw_hue(180.0f, 1.0f, BAND_VAL));
    canvas_add_arc(c, rot + 90.0f,  BAND_DEG, rgbw_hue(300.0f, 1.0f, BAND_VAL));
    canvas_add_arc(c, rot + 180.0f, BAND_DEG, rgbw_hue(60.0f,  1.0f, BAND_VAL));
}

/* Printing — "The CMYK Ink Roller".
 *
 * A "working, please wait" indicator for an unknown duration. The mode manager
 * enforces the 120 s timeout; a jammed printer must not leave the ring
 * cheerfully rolling ink forever.
 *
 * The fourth quadrant is what makes the rotation legible — a continuous colour
 * wheel at this speed reads as a blur. It carries a dim pistachio rather than
 * genuine off: a quarter of the ring going black looks like a dead segment,
 * not like a design, and that is exactly how it read on the real strip. The
 * bands stay 3x brighter, so the quadrant reads as the wheel's quiet fourth
 * colour and not as a fourth ink.
 *
 * Steady speed and constant direction; resist making it accelerate.
 */
#include "anim.h"

#define ROTATE_MS 2000
#define BAND_DEG  90.0f
#define BAND_VAL  0.45f

/* Pistachio (#93C572) as display-referred HSV — rgbw_hue linearizes. Value is
 * pulled well under BAND_VAL; hue and saturation are the colour's own, so it
 * darkens without drifting toward the yellow band beside it. */
#define GAP_HUE   96.0f
#define GAP_SAT   0.42f
#define GAP_VAL   0.15f

void anim_printing(uint32_t t, const mode_params_t *p, canvas_t *c)
{
    (void)p;

    const float rot = (float)(t % ROTATE_MS) / (float)ROTATE_MS * 360.0f;

    /* Cyan, magenta, yellow, then pistachio where the gap used to be. The four
     * arcs tile the full 360°, so no pixel is left at genuine zero. */
    canvas_add_arc(c, rot + 0.0f,   BAND_DEG, rgbw_hue(180.0f, 1.0f, BAND_VAL));
    canvas_add_arc(c, rot + 90.0f,  BAND_DEG, rgbw_hue(300.0f, 1.0f, BAND_VAL));
    canvas_add_arc(c, rot + 180.0f, BAND_DEG, rgbw_hue(60.0f,  1.0f, BAND_VAL));
    canvas_add_arc(c, rot + 270.0f, BAND_DEG, rgbw_hue(GAP_HUE, GAP_SAT, GAP_VAL));
}

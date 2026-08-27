/* Test — "One Die At A Time".
 *
 * Not a booth mode. This is the bench instrument: a flat, full-scale fill of a
 * single physical die, so an operator can walk the ring and see which pixel is
 * dead, miswired or colour-swapped. Boot's four laps prove the same thing but
 * only once, at power-on, and only if you happen to be watching.
 *
 * Three deliberate choices:
 *
 *  1. ONE CHANNEL, NOTHING ELSE. rgbw_hue never lights W and desaturates below
 *     sat 1.0, so PHASE cannot express "pure red" or "W only" at all. A test
 *     that mixes dies cannot tell you which one failed.
 *
 *  2. FULL SCALE, GLOBAL BRIGHTNESS BYPASSED (see render_frame). A dim die and
 *     a dead one look alike at 70%.
 *
 *  3. FLAT. Any animation here would hide exactly the single-pixel defect this
 *     exists to find.
 *
 * It holds at the same current as Capture in the white case, so it carries the
 * same safety deadline — see MODE_TEST_TIMEOUT_MS.
 */
#include "anim.h"

void anim_test(uint32_t t, const mode_params_t *p, canvas_t *c)
{
    (void)t;

    /* Written straight into the canvas rather than through rgbw_hue/rgbw_white,
     * which both apply the display-referred GAMMA. Here 1.0 must mean "this die
     * fully on", not "1.0 of a perceptual scale". */
    rgbw_t px = {0};
    switch (p->code) {
        case TEST_CH_RED:   px.r = 1.0f; break;
        case TEST_CH_GREEN: px.g = 1.0f; break;
        case TEST_CH_BLUE:  px.b = 1.0f; break;
        case TEST_CH_WHITE: px.w = 1.0f; break;
        default:
            /* All four. Worth having: it is the only way to see a die that
             * works alone but is shorted to a neighbour. */
            px.r = px.g = px.b = px.w = 1.0f;
            break;
    }

    canvas_fill(c, px);
}

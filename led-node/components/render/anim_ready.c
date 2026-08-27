/* Ready — "The Parked Hand".
 *
 * The beat between the guest choosing a layout and the countdown actually
 * starting. That gap is the camera warming up — 1-3.5 s of the preview stream
 * painting its first frame — and it is owned by the browser, so its length is
 * not knowable here or on the Pi.
 *
 * Deliberately the countdown's own vocabulary held still: the same dim wash,
 * the same head, parked at 12 o'clock. When COUNTDOWN arrives the head simply
 * starts moving. That makes the start of the count unmistakable in peripheral
 * vision, which is the whole problem this mode exists to solve — a ring that
 * was already sweeping before the numbers appeared read as unrelated to them.
 *
 * Static on purpose. Anything breathing here would compete with Idle, and this
 * is the one moment the guest is being asked to look at the lens rather than at
 * the ring.
 */
#include "anim.h"

#define BACKGROUND 0.06f
#define HEAD_VAL   0.45f
#define HEAD_DEG   0.0f    /* 12 o'clock — where the countdown sweep begins */

void anim_ready(uint32_t t, const mode_params_t *p, canvas_t *c)
{
    (void)t;
    (void)p;

    /* Matches anim_countdown's BACKGROUND exactly, so the transition into the
     * count changes only what is moving, not the level around it. */
    canvas_fill(c, rgbw_white(BACKGROUND));

    /* Dimmer than the countdown head (0.70): this one is at rest. The lift when
     * the sweep begins is part of the cue. */
    canvas_add_point(c, HEAD_DEG, rgbw_white(HEAD_VAL), DEG_PER_PIXEL * 1.2f);
}

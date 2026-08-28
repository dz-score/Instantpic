/* Countdown — "The Ticking Clock". Spec: Docs/LED_SPEC.md §3.
 *
 * 60 px at one rev/s is one pixel per 16.7 ms, so the head must be drawn with
 * an anti-aliased primitive rather than snapped to a pixel or the sweep looks
 * steppy.
 *
 * This must stay count-agnostic: it is given a duration and spins. The
 * final-second lift comes from comparing elapsed to duration, which is the only
 * reason it needs no knowledge of the count. Keep it that way — a countdown
 * length then stays a parameter rather than a reflash.
 */
#include <stdbool.h>

#include "anim.h"

#define REV_MS          1000
#define BACKGROUND      0.06f
#define TRAIL_DEG       40.0f
#define TRAIL_DEG_FINAL 90.0f
#define HEAD_VAL        0.70f
#define HEAD_VAL_FINAL  1.00f
#define TRAIL_STEP_DEG  3.0f

void anim_countdown(uint32_t t, const mode_params_t *p, canvas_t *c)
{
    if (p->duration_ms > 0 && t > p->duration_ms) {
        t = p->duration_ms;
    }

    const bool final_second =
        p->duration_ms > 0 && (p->duration_ms - t) <= REV_MS;

    const float trail_deg = final_second ? TRAIL_DEG_FINAL : TRAIL_DEG;
    const float head_val  = final_second ? HEAD_VAL_FINAL : HEAD_VAL;

    /* Dim wash, W-tinted — must not fight the white key light that follows. */
    canvas_fill(c, rgbw_white(BACKGROUND));

    const float head_deg = (float)(t % REV_MS) / (float)REV_MS * 360.0f;

    /* Trail, drawn as overlapping anti-aliased points so it falls off smoothly
     * instead of ending on a hard edge. */
    for (float d = trail_deg; d > 0.0f; d -= TRAIL_STEP_DEG) {
        const float fade = 1.0f - d / trail_deg;
        canvas_add_point(c, head_deg - d,
                         rgbw_white(head_val * fade * fade * 0.55f),
                         DEG_PER_PIXEL * 1.6f);
    }

    canvas_add_point(c, head_deg, rgbw_white(head_val), DEG_PER_PIXEL * 1.2f);
}

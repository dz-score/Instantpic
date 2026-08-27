/* Countdown — "The Ticking Clock".
 *
 * One revolution per second. At 60 px that is a sweeping second hand, one pixel
 * per 16.7 ms — which is why the head is drawn with an anti-aliased primitive
 * rather than snapped to a pixel.
 *
 * The ring deliberately does NOT encode which second it is; the screen displays
 * the number, and duplicating it in the periphery buys nothing. That also keeps
 * this count-agnostic: it gets a duration and spins, so a 5-second countdown is
 * a parameter rather than a code change.
 *
 * The final-second lift is not information either. The guest is looking at the
 * lens, and peripheral vision is poor at reading numbers and excellent at
 * noticing "that got brighter" — it is a nudge to smile now rather than half a
 * second late. It comes from comparing elapsed to duration, so this still never
 * learns the count.
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

    /* A dim wash so the ring reads as present rather than off. Kept W-tinted:
     * this is the half-second before a white key light comes on, and a coloured
     * trail would fight it. */
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

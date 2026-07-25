/* Playful — "The Interactive Guide".
 *
 * Acknowledges that the guest advanced a step in the UI flow. The firmware
 * never learns the screen list: the Pi sends a colour, this owns the
 * transition. Screens can be added, removed or retuned without a reflash.
 *
 * Two parts: a head sweeps once around leaving the new hue behind it, then it
 * settles into a hold quiet enough to sit behind screen-reading. The sweep
 * always runs the same direction — consistent direction reads as progress.
 */
#include <math.h>

#include "anim.h"

#define SWEEP_MS    400
#define SHIMMER_MS  3000
#define HOLD_SAT    0.75f
#define HOLD_VAL    0.34f

typedef struct {
    float hue;
    float val;
} hold_ctx_t;

static rgbw_t hold_field(float deg, void *vctx)
{
    const hold_ctx_t *ctx = (const hold_ctx_t *)vctx;
    (void)deg;
    return rgbw_hue(ctx->hue, HOLD_SAT, ctx->val);
}

void anim_playful(uint32_t t, const mode_params_t *p, canvas_t *c)
{
    if (t < SWEEP_MS) {
        const float progress = (float)t / (float)SWEEP_MS;
        const float head_deg = progress * 360.0f;

        /* The hue is laid down behind the head. */
        canvas_add_arc(c, 0.0f, head_deg, rgbw_hue(p->hue, HOLD_SAT, HOLD_VAL));
        canvas_add_point(c, head_deg, rgbw_hue(p->hue, 0.35f, 0.9f), DEG_PER_PIXEL * 2.0f);
        return;
    }

    hold_ctx_t ctx = {
        .hue = p->hue,
        .val = HOLD_VAL + 0.04f * sinf(2.0f * (float)M_PI *
                                       (float)((t - SWEEP_MS) % SHIMMER_MS) / (float)SHIMMER_MS),
    };
    canvas_add_field(c, hold_field, &ctx);
}

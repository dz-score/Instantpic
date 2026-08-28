/* Idle — "The Ambient Beacon". Spec: Docs/LED_SPEC.md §1.
 *
 * Sets the thermal baseline (~0.4 A) since it runs most of the night.
 *
 * Depth is set by RATIO, not by an absolute swing. Canvas values are linear
 * light and output.c encodes with 1/2.2, so perceived brightness goes roughly
 * as the cube root: 0.25..0.40 is a 1.6x linear ratio and read as barely moving
 * on the real strip. 0.12..0.48 is 4x, about 1.6x perceived — unmistakably a
 * breath. The mean is 0.30 rather than 0.325, so the thermal figure above holds.
 */
#include <math.h>

#include "anim.h"

#define BREATHE_MS   6000
#define ROTATE_MS    25000
#define BASE_HUE     32.0f    /* warm */
#define HUE_RANGE    18.0f
#define TINT_SAT     0.55f
#define TINT_VAL     0.16f
#define BREATHE_MID  0.30f
#define BREATHE_AMP  0.18f

typedef struct {
    float rotation;
    float breathe;
    float tint;
} idle_ctx_t;

static rgbw_t idle_field(float deg, void *vctx)
{
    const idle_ctx_t *ctx = (const idle_ctx_t *)vctx;

    const float phase = (deg + ctx->rotation) * (float)M_PI / 180.0f;
    const float hue   = BASE_HUE + HUE_RANGE * sinf(phase);

    return rgbw_add(rgbw_white(ctx->breathe),
                    rgbw_hue(hue, TINT_SAT, ctx->tint));
}

void anim_idle(uint32_t t, const mode_params_t *p, canvas_t *c)
{
    (void)p;

    const float breathe = BREATHE_MID +
        BREATHE_AMP * sinf(2.0f * (float)M_PI * (float)(t % BREATHE_MS) /
                           (float)BREATHE_MS);

    idle_ctx_t ctx = {
        .rotation = (float)(t % ROTATE_MS) / (float)ROTATE_MS * 360.0f,
        .breathe  = breathe,
        /* The tint breathes with the white rather than sitting at a constant
         * level. Held constant it acts as a floor under the swing, which is
         * what flattened the effect: the ring's total output only moved 1.37x
         * even though the white channel moved 1.6x. */
        .tint     = TINT_VAL * (breathe / BREATHE_MID),
    };

    canvas_add_field(c, idle_field, &ctx);
}

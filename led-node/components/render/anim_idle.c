/* Idle — "The Ambient Beacon".
 *
 * Runs most of the night, so it sets the thermal baseline (~0.4 A) and it has
 * to survive four hours of being looked at. The tuning rule that matters:
 * nothing here has a period under ~5 s. Fast motion reads as urgent, and urgent
 * for four hours is exhausting to be near.
 *
 * W-dominant with colour as a tint. Saturated RGB at idle is what makes
 * installations look cheap.
 */
#include <math.h>

#include "anim.h"

#define BREATHE_MS   6000
#define ROTATE_MS    25000
#define BASE_HUE     32.0f    /* warm */
#define HUE_RANGE    18.0f
#define TINT_SAT     0.55f
#define TINT_VAL     0.16f

typedef struct {
    float rotation;
    float breathe;
} idle_ctx_t;

static rgbw_t idle_field(float deg, void *vctx)
{
    const idle_ctx_t *ctx = (const idle_ctx_t *)vctx;

    const float phase = (deg + ctx->rotation) * (float)M_PI / 180.0f;
    const float hue   = BASE_HUE + HUE_RANGE * sinf(phase);

    return rgbw_add(rgbw_white(ctx->breathe),
                    rgbw_hue(hue, TINT_SAT, TINT_VAL));
}

void anim_idle(uint32_t t, const mode_params_t *p, canvas_t *c)
{
    (void)p;

    idle_ctx_t ctx = {
        .rotation = (float)(t % ROTATE_MS) / (float)ROTATE_MS * 360.0f,
        .breathe  = 0.325f + 0.075f * sinf(2.0f * (float)M_PI * (float)(t % BREATHE_MS) /
                                           (float)BREATHE_MS),
    };

    canvas_add_field(c, idle_field, &ctx);
}

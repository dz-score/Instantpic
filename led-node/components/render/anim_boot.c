/* Boot / pre-link. Spec: Docs/LED_SPEC.md §8.
 *
 * The four laps are the wiring self-test. Do not shorten them to "look
 * better" — that check is the reason this pattern is what it is.
 */
#include "anim.h"

#define SWEEP_MS   500
#define SWEEP_TOTAL (4 * SWEEP_MS)
#define ORBIT_MS   4000

void anim_boot(uint32_t t, const mode_params_t *p, canvas_t *c)
{
    (void)p;

    if (t < SWEEP_TOTAL) {
        const int   channel  = (int)(t / SWEEP_MS);
        const float progress = (float)(t % SWEEP_MS) / (float)SWEEP_MS;

        rgbw_t color;
        switch (channel) {
            case 0:  color = rgbw_make(1.0f, 0.0f, 0.0f, 0.0f); break;
            case 1:  color = rgbw_make(0.0f, 1.0f, 0.0f, 0.0f); break;
            case 2:  color = rgbw_make(0.0f, 0.0f, 1.0f, 0.0f); break;
            default: color = rgbw_make(0.0f, 0.0f, 0.0f, 1.0f); break;
        }

        canvas_add_arc(c, 0.0f, progress * 360.0f, color);
        return;
    }

    /* Waiting, not broken. */
    const uint32_t since = t - SWEEP_TOTAL;
    const float    deg   = (float)(since % ORBIT_MS) / (float)ORBIT_MS * 360.0f;
    canvas_add_point(c, deg, rgbw_white(0.25f), DEG_PER_PIXEL * 1.5f);
}

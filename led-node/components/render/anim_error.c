/* Error — "The Maintenance Heartbeat". Spec: Docs/LED_SPEC.md §7.
 *
 * The code is the number of double-pulse groups before the longer pause; that
 * is the whole diagnostic, so keep the grouping legible.
 *
 * Must stay visually distinct from Link Lost — opposite diagnoses, and at a
 * venue this is the only debugging output there is.
 */
#include <math.h>

#include "anim.h"

#define BEAT_MS   180
#define BEAT_GAP  260     /* start of the second beat within a group */
#define GROUP_MS  800
#define PAUSE_MS  1200
#define MAX_LEVEL 0.15f
#define RED_HUE   0.0f
#define MAX_GROUPS 9

void anim_error(uint32_t t, const mode_params_t *p, canvas_t *c)
{
    uint32_t groups = 1;
    if (p->code > 0) {
        groups = (uint32_t)p->code;
        if (groups > MAX_GROUPS) {
            groups = MAX_GROUPS;
        }
    }

    const uint32_t beats_ms = groups * GROUP_MS;
    const uint32_t cycle    = beats_ms + PAUSE_MS;
    const uint32_t x        = t % cycle;

    float level = 0.0f;
    if (x < beats_ms) {
        const uint32_t in = x % GROUP_MS;
        if (in < BEAT_MS) {
            level = sinf((float)M_PI * (float)in / (float)BEAT_MS);
        } else if (in >= BEAT_GAP && in < BEAT_GAP + BEAT_MS) {
            level = sinf((float)M_PI * (float)(in - BEAT_GAP) / (float)BEAT_MS);
        }
    }

    canvas_fill(c, rgbw_hue(RED_HUE, 1.0f, MAX_LEVEL * level));
}

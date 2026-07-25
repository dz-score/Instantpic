/* Error — "The Maintenance Heartbeat".
 *
 * Tells the operator something is wrong without alarming a room full of guests:
 * deep red, low brightness, deliberately calm.
 *
 * The code is expressed as the number of double-pulse groups before a longer
 * pause, which gives on-site diagnosis without a laptop — worth a lot at 6pm at
 * a venue.
 *
 * Must stay visually distinct from Link Lost. These are opposite diagnoses: this
 * one is the host reporting a fault, that one is the node noticing the host went
 * silent.
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

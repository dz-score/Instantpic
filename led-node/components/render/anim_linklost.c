/* Link Lost — the node noticing the host went silent.
 *
 * Distinct from Error in both colour and character: amber rather than red, a
 * smooth symmetric pulse rather than a heartbeat, no motion and no direction.
 * At the venue this visual is the only debugging output there is, so the two
 * must never be confused.
 *
 * The safety rule that gets here from Capture lives in modes.c: a dead host must
 * never strand the strip at full white, the highest-current and highest-heat
 * state in the system, held indefinitely with nobody watching.
 */
#include <math.h>

#include "anim.h"

#define PULSE_MS   2000
#define LEVEL_MIN  0.10f
#define LEVEL_MAX  0.30f
#define AMBER_HUE  35.0f

void anim_linklost(uint32_t t, const mode_params_t *p, canvas_t *c)
{
    (void)p;

    /* Symmetric: rises and falls at the same rate, so it reads as "waiting"
     * rather than as a repeating alarm. */
    const float phase = 2.0f * (float)M_PI * (float)(t % PULSE_MS) / (float)PULSE_MS;
    const float unit  = 0.5f - 0.5f * cosf(phase);
    const float level = LEVEL_MIN + (LEVEL_MAX - LEVEL_MIN) * unit;

    canvas_fill(c, rgbw_hue(AMBER_HUE, 0.9f, level));
}

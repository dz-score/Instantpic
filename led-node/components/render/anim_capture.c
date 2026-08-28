/* Capture — "The Studio Over-Drive". Spec: Docs/LED_SPEC.md §4.
 *
 * This is photographic equipment, not decoration. Three invariants:
 *
 *  1. FULL BRIGHTNESS ONLY. Global brightness is deliberately bypassed for this
 *     mode in output_show's apply_brightness. Do not re-enable it — dimming is
 *     PWM and the shutter will sample a fraction of a cycle.
 *  2. W CHANNEL, NOT MIXED RGB WHITE. RGB here is a small fixed temperature
 *     trim set once at install, nothing more.
 *  3. ZERO ANIMATION once ramped.
 *
 * The 100 ms ramp is for the PSU, not the eye — keep it.
 */
#include "anim.h"

#define RAMP_MS 100

/* Fixed colour-temperature trim, set once at installation. Nudges the white;
 * never used to synthesize it. Keep these small. */
#define TRIM_R 0.00f
#define TRIM_G 0.00f
#define TRIM_B 0.00f

void anim_capture(uint32_t t, const mode_params_t *p, canvas_t *c)
{
    (void)p;

    const float ramp = t >= RAMP_MS ? 1.0f : (float)t / (float)RAMP_MS;

    rgbw_t light = rgbw_white(ramp);
    light.r      = TRIM_R * ramp;
    light.g      = TRIM_G * ramp;
    light.b      = TRIM_B * ramp;

    canvas_fill(c, light);
}

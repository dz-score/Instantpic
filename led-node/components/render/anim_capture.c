/* Capture — "The Studio Over-Drive".
 *
 * This is the key light. Every other animation is decoration; this one is
 * photographic equipment. Three things here are load-bearing:
 *
 *  1. FULL BRIGHTNESS ONLY. Global brightness is bypassed for this mode
 *     (see output_show's apply_brightness). SK6812 dimming is PWM in the high
 *     hundreds of Hz; at 1/200 s or faster the shutter samples a fraction of a
 *     cycle and you get banding plus shot-to-shot exposure drift. If less light
 *     is needed, move the strip or stop down.
 *
 *  2. W CHANNEL, NOT MIXED RGB WHITE. Mixed white is a three-spike spectrum: it
 *     renders skin blotchy and makes fabric colour unrecoverable in post. RGB
 *     is reserved for a small fixed temperature trim, set once at install.
 *
 *  3. ZERO ANIMATION once ramped. Any motion means uneven lighting across a
 *     burst.
 *
 * The 100 ms ramp is imperceptible on camera and much kinder to the PSU and
 * inrush than snapping from an idle animation to near-max current.
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

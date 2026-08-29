/* Finished — "The Hollywood Sparkle". Spec: Docs/LED_SPEC.md §6.
 *
 * Sparkles are on the W channel; on mixed RGB they read as pixels.
 *
 * Randomness without breaking purity: time is divided into fixed slots, each
 * slot's sparkle is derived by hashing the slot index, and every slot still
 * alive at time t is re-derived and evaluated. Same t always yields the same
 * frame, so this stays cross-fadeable and unit-testable. Anything stateful here
 * breaks both.
 */
#include <math.h>

#include "anim.h"

#define SLOT_MS    30
#define LIFE_MS    430
#define ATTACK_MS  30
#define DECAY_TAU  150.0f
#define BASE_LEVEL 0.40f
#define BASE_HUE   35.0f
#define BASE_TINT  0.10f

static uint32_t hash32(uint32_t x)
{
    x ^= x >> 16;
    x *= 0x7feb352dU;
    x ^= x >> 15;
    x *= 0x846ca68bU;
    x ^= x >> 16;
    return x;
}

void anim_finished(uint32_t t, const mode_params_t *p, canvas_t *c)
{
    /* Warm base. */
    canvas_fill(c, rgbw_add(rgbw_white(BASE_LEVEL), rgbw_hue(BASE_HUE, 0.8f, BASE_TINT)));

    /* Density falls from full to nothing across the mode. */
    float density = 1.0f;
    if (p->duration_ms > 0) {
        density = 1.0f - (float)t / (float)p->duration_ms;
        if (density < 0.0f) {
            density = 0.0f;
        }
    }

    const int64_t newest = (int64_t)t / SLOT_MS;
    const int64_t oldest = ((int64_t)t - LIFE_MS) / SLOT_MS;

    for (int64_t slot = oldest; slot <= newest; slot++) {
        if (slot < 0) {
            continue;
        }

        const uint32_t h = hash32((uint32_t)slot);

        /* Probabilistic spawn, thinning out as density decays. */
        if ((h & 0xffU) >= (uint32_t)(255.0f * density)) {
            continue;
        }

        const int64_t age = (int64_t)t - slot * SLOT_MS;
        if (age < 0 || age > LIFE_MS) {
            continue;
        }

        /* Fast attack, slower decay. */
        const float env = age < ATTACK_MS
                              ? (float)age / (float)ATTACK_MS
                              : expf(-(float)(age - ATTACK_MS) / DECAY_TAU);

        const float deg = (float)(hash32(h) % 3600U) / 10.0f;
        canvas_add_point(c, deg, rgbw_white(env), DEG_PER_PIXEL * 1.3f);
    }
}

#include "canvas.h"

#include <math.h>
#include <string.h>

/* Display-referred -> linear. The inverse lives in output.c. */
#define GAMMA 2.2f

float canvas_wrap_deg(float deg)
{
    deg = fmodf(deg, 360.0f);
    if (deg < 0.0f) {
        deg += 360.0f;
    }
    return deg;
}

float canvas_angular_distance(float a, float b)
{
    const float d = canvas_wrap_deg(a - b);
    return d > 180.0f ? 360.0f - d : d;
}

rgbw_t rgbw_make(float r, float g, float b, float w)
{
    return (rgbw_t){.r = r, .g = g, .b = b, .w = w};
}

rgbw_t rgbw_scale(rgbw_t c, float k)
{
    return (rgbw_t){.r = c.r * k, .g = c.g * k, .b = c.b * k, .w = c.w * k};
}

rgbw_t rgbw_add(rgbw_t a, rgbw_t b)
{
    return (rgbw_t){.r = a.r + b.r, .g = a.g + b.g, .b = a.b + b.b, .w = a.w + b.w};
}

rgbw_t rgbw_lerp(rgbw_t a, rgbw_t b, float t)
{
    return (rgbw_t){
        .r = a.r + (b.r - a.r) * t,
        .g = a.g + (b.g - a.g) * t,
        .b = a.b + (b.b - a.b) * t,
        .w = a.w + (b.w - a.w) * t,
    };
}

rgbw_t rgbw_hue(float hue_deg, float sat, float val)
{
    const float h = canvas_wrap_deg(hue_deg) / 60.0f;
    const float c = val * sat;
    const float x = c * (1.0f - fabsf(fmodf(h, 2.0f) - 1.0f));
    const float m = val - c;

    float r = 0.0f, g = 0.0f, b = 0.0f;
    switch ((int)h) {
        case 0:  r = c; g = x; b = 0; break;
        case 1:  r = x; g = c; b = 0; break;
        case 2:  r = 0; g = c; b = x; break;
        case 3:  r = 0; g = x; b = c; break;
        case 4:  r = x; g = 0; b = c; break;
        default: r = c; g = 0; b = x; break;
    }

    /* HSV is display-referred; the canvas is linear. */
    return (rgbw_t){
        .r = powf(r + m, GAMMA),
        .g = powf(g + m, GAMMA),
        .b = powf(b + m, GAMMA),
        .w = 0.0f,
    };
}

rgbw_t rgbw_white(float level)
{
    if (level < 0.0f) {
        level = 0.0f;
    }
    return (rgbw_t){.r = 0.0f, .g = 0.0f, .b = 0.0f, .w = powf(level, GAMMA)};
}

void canvas_clear(canvas_t *c)
{
    memset(c->px, 0, sizeof(c->px));
}

void canvas_fill(canvas_t *c, rgbw_t color)
{
    for (int i = 0; i < RING_LEDS; i++) {
        c->px[i] = color;
    }
}

void canvas_scale(canvas_t *c, float k)
{
    for (int i = 0; i < RING_LEDS; i++) {
        c->px[i] = rgbw_scale(c->px[i], k);
    }
}

void canvas_blend(canvas_t *dst, const canvas_t *a, const canvas_t *b, float t)
{
    for (int i = 0; i < RING_LEDS; i++) {
        dst->px[i] = rgbw_lerp(a->px[i], b->px[i], t);
    }
}

/* Angle at the centre of pixel i. */
static inline float pixel_center_deg(int i)
{
    return ((float)i + 0.5f) * DEG_PER_PIXEL;
}

void canvas_add_point(canvas_t *c, float deg, rgbw_t color, float falloff_deg)
{
    if (falloff_deg <= 0.0f) {
        falloff_deg = DEG_PER_PIXEL;
    }

    for (int i = 0; i < RING_LEDS; i++) {
        const float d = canvas_angular_distance(pixel_center_deg(i), deg);
        if (d >= falloff_deg) {
            continue;
        }
        /* Smoothstep, so the head glides rather than stepping. */
        float wgt = 1.0f - d / falloff_deg;
        wgt       = wgt * wgt * (3.0f - 2.0f * wgt);
        c->px[i]  = rgbw_add(c->px[i], rgbw_scale(color, wgt));
    }
}

/* Length of the overlap between [lo,hi] and [a,b]. */
static inline float span_overlap(float lo, float hi, float a, float b)
{
    const float l = lo > a ? lo : a;
    const float h = hi < b ? hi : b;
    return h > l ? h - l : 0.0f;
}

void canvas_add_arc(canvas_t *c, float deg_start, float deg_span, rgbw_t color)
{
    if (deg_span <= 0.0f) {
        return;
    }
    if (deg_span > 360.0f) {
        deg_span = 360.0f;
    }

    const float half = DEG_PER_PIXEL * 0.5f;

    for (int i = 0; i < RING_LEDS; i++) {
        /* Pixel position relative to the arc start, so the arc lives in a
         * frame where it simply runs [0, span]. */
        const float rel = canvas_wrap_deg(pixel_center_deg(i) - deg_start);

        /* Test the pixel twice — once in [0,360) and once shifted below zero —
         * so a pixel straddling the wrap point is still covered. */
        float covered = span_overlap(rel - half, rel + half, 0.0f, deg_span) +
                        span_overlap(rel - 360.0f - half, rel - 360.0f + half, 0.0f, deg_span);

        if (covered <= 0.0f) {
            continue;
        }
        const float wgt = covered / DEG_PER_PIXEL;
        c->px[i]        = rgbw_add(c->px[i], rgbw_scale(color, wgt > 1.0f ? 1.0f : wgt));
    }
}

void canvas_add_field(canvas_t *c, canvas_field_fn fn, void *ctx)
{
    for (int i = 0; i < RING_LEDS; i++) {
        c->px[i] = rgbw_add(c->px[i], fn(pixel_center_deg(i), ctx));
    }
}

/* Canvas maths — the two invariants the design leans on hardest.
 *
 * Sub-pixel rendering and linear-space blending are both argued for at length
 * in canvas.h. Neither is visible in a code review of an animation, because
 * both live in the primitives — which is exactly why they are worth asserting.
 */
#include "canvas.h"
#include "test.h"

static float total_light(const canvas_t *c)
{
    float sum = 0.0f;
    for (int i = 0; i < RING_LEDS; i++) {
        sum += c->px[i].r + c->px[i].g + c->px[i].b + c->px[i].w;
    }
    return sum;
}

TEST(wrap_brings_any_bearing_into_range)
{
    CHECK_NEAR(canvas_wrap_deg(0.0f), 0.0, 0.001);
    CHECK_NEAR(canvas_wrap_deg(360.0f), 0.0, 0.001);
    CHECK_NEAR(canvas_wrap_deg(370.0f), 10.0, 0.001);
    CHECK_NEAR(canvas_wrap_deg(-10.0f), 350.0, 0.001);
    CHECK_NEAR(canvas_wrap_deg(-370.0f), 350.0, 0.001);
}

TEST(angular_distance_is_the_short_way_round)
{
    CHECK_NEAR(canvas_angular_distance(10.0f, 350.0f), 20.0, 0.001);
    CHECK_NEAR(canvas_angular_distance(0.0f, 180.0f), 180.0, 0.001);
    CHECK_NEAR(canvas_angular_distance(90.0f, 90.0f), 0.0, 0.001);
}

TEST(blend_endpoints_are_exact)
{
    /* t=0 and t=1 must reproduce their inputs bit for bit, or a cross-fade
     * would visibly jump at its start and end. */
    canvas_t a, b, out;
    canvas_fill(&a, rgbw_white(1.0f));
    canvas_clear(&b);

    canvas_blend(&out, &a, &b, 0.0f);
    CHECK_NEAR(out.px[0].w, 1.0, 0.0001);

    canvas_blend(&out, &a, &b, 1.0f);
    CHECK_NEAR(out.px[0].w, 0.0, 0.0001);
}

TEST(blend_midpoint_is_linear_not_gamma_encoded)
{
    /* The whole reason the canvas holds linear light. Blending two
     * gamma-encoded buffers makes a fade duck dark through its midpoint; in
     * linear space the midpoint is the arithmetic mean. */
    canvas_t a, b, out;
    canvas_fill(&a, rgbw_white(1.0f));
    canvas_clear(&b);

    canvas_blend(&out, &a, &b, 0.5f);
    CHECK_NEAR(out.px[0].w, 0.5, 0.0001);
}

TEST(a_point_between_pixels_lights_both)
{
    /* Sub-pixel rendering, stated as a property rather than a pixel pattern:
     * a head placed exactly on a boundary must not snap to one side. At 60 px
     * and one revolution per second the head moves one pixel per 16.7 ms, and
     * snapping is plainly visible. */
    canvas_t c;
    canvas_clear(&c);
    canvas_add_point(&c, DEG_PER_PIXEL * 0.5f, rgbw_white(1.0f), DEG_PER_PIXEL);

    CHECK(c.px[0].w > 0.0f);
    CHECK(c.px[1].w > 0.0f);
    CHECK_NEAR(c.px[0].w, c.px[1].w, 0.05);
}

TEST(a_point_on_a_pixel_centre_is_concentrated_there)
{
    canvas_t c;
    canvas_clear(&c);
    canvas_add_point(&c, 0.0f, rgbw_white(1.0f), DEG_PER_PIXEL);

    CHECK(c.px[0].w > c.px[1].w);
    CHECK(c.px[0].w > c.px[RING_LEDS - 1].w);
}

TEST(a_point_carries_the_same_light_wherever_it_sits)
{
    /* Total energy must not flicker as the head crosses pixel boundaries —
     * that flicker is what naive rounding produces, and it reads as a stutter
     * in a slow sweep. */
    canvas_t on_centre, off_centre;

    canvas_clear(&on_centre);
    canvas_add_point(&on_centre, 0.0f, rgbw_white(1.0f), DEG_PER_PIXEL * 1.5f);

    canvas_clear(&off_centre);
    canvas_add_point(&off_centre, DEG_PER_PIXEL * 0.5f, rgbw_white(1.0f),
                     DEG_PER_PIXEL * 1.5f);

    CHECK_NEAR(total_light(&on_centre), total_light(&off_centre), 0.05);
}

TEST(an_arc_spans_from_start_across_span_degrees)
{
    /* deg_span, not deg_end — the distinction the architecture doc got wrong
     * for several commits. A quarter-turn arc from 0 lights a quarter of the
     * ring, not the whole thing. */
    canvas_t c;
    canvas_clear(&c);
    canvas_add_arc(&c, 0.0f, 90.0f, rgbw_white(1.0f));

    int lit = 0;
    for (int i = 0; i < RING_LEDS; i++) {
        if (c.px[i].w > 0.01f) { lit++; }
    }
    CHECK(lit >= RING_LEDS / 4 - 2);
    CHECK(lit <= RING_LEDS / 4 + 2);
}

TEST(white_uses_the_white_channel_alone)
{
    /* Mixing R+G+B to white gives a three-spike spectrum that renders skin
     * blotchy — the reason Capture drives W. */
    const rgbw_t w = rgbw_white(0.8f);
    CHECK_NEAR(w.w, 0.8, 0.0001);
    CHECK_NEAR(w.r, 0.0, 0.0001);
    CHECK_NEAR(w.g, 0.0, 0.0001);
    CHECK_NEAR(w.b, 0.0, 0.0001);
}

TEST(hue_returns_linear_light)
{
    /* rgbw_hue linearizes its display-referred HSV result, so a mid value must
     * come back below the halfway point, not at it. */
    const rgbw_t mid = rgbw_hue(0.0f, 1.0f, 0.5f);
    CHECK(mid.r > 0.0f);
    CHECK(mid.r < 0.5f);
    CHECK_NEAR(mid.w, 0.0, 0.0001);
}

int main(void)
{
    RUN(wrap_brings_any_bearing_into_range);
    RUN(angular_distance_is_the_short_way_round);
    RUN(blend_endpoints_are_exact);
    RUN(blend_midpoint_is_linear_not_gamma_encoded);
    RUN(a_point_between_pixels_lights_both);
    RUN(a_point_on_a_pixel_centre_is_concentrated_there);
    RUN(a_point_carries_the_same_light_wherever_it_sits);
    RUN(an_arc_spans_from_start_across_span_degrees);
    RUN(white_uses_the_white_channel_alone);
    RUN(hue_returns_linear_light);
    return test_report("canvas");
}

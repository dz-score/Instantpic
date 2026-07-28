#include "output.h"

#include <math.h>
#include <string.h>

#include "esp_check.h"
#include "esp_log.h"
#include "led_strip.h"

static const char *TAG = "output";

#define INV_GAMMA (1.0f / 2.2f)

/* An unset Kconfig bool emits no #define at all, so the bare symbol is an
 * undeclared identifier in any runtime expression (it only evaluates to 0
 * inside #if). Normalize it once here. */
#if CONFIG_LED_NODE_RING_REVERSED
#define RING_REVERSED 1
#else
#define RING_REVERSED 0
#endif

static led_strip_handle_t s_strip;
static float              s_brightness = CONFIG_LED_NODE_DEFAULT_BRIGHTNESS / 100.0f;

esp_err_t output_init(void)
{
    led_strip_config_t strip_config = {
        .strip_gpio_num         = CONFIG_LED_NODE_DATA_GPIO,
        .max_leds               = RING_LEDS,
        .led_model              = LED_MODEL_SK6812,
        .color_component_format = LED_STRIP_COLOR_COMPONENT_FMT_GRBW,
        .flags.invert_out       = false,
    };

    led_strip_rmt_config_t rmt_config = {
        .clk_src           = RMT_CLK_SRC_DEFAULT,
        .resolution_hz     = 10 * 1000 * 1000,   /* 10 MHz -> 0.1 us resolution */
        .mem_block_symbols = 64,
        .flags.with_dma    = false,
    };

    ESP_RETURN_ON_ERROR(led_strip_new_rmt_device(&strip_config, &rmt_config, &s_strip),
                        TAG, "led_strip_new_rmt_device failed");

    led_strip_clear(s_strip);
    ESP_LOGI(TAG, "%d px on GPIO %d, offset %d%s",
             RING_LEDS, CONFIG_LED_NODE_DATA_GPIO, CONFIG_LED_NODE_RING_OFFSET,
             RING_REVERSED ? ", reversed" : "");
    return ESP_OK;
}

void output_set_brightness(float level)
{
    if (level < 0.0f) {
        level = 0.0f;
    } else if (level > 1.0f) {
        level = 1.0f;
    }
    s_brightness = level;
}

float output_get_brightness(void)
{
    return s_brightness;
}

/* Logical index (increasing clockwise from 12 o'clock) -> physical pixel. */
static inline int physical_index(int logical)
{
#if RING_REVERSED
    int p = CONFIG_LED_NODE_RING_OFFSET - logical;
#else
    int p = CONFIG_LED_NODE_RING_OFFSET + logical;
#endif
    p %= RING_LEDS;
    if (p < 0) {
        p += RING_LEDS;
    }
    return p;
}

/* Linear light -> 8-bit display value.
 *
 * Computed directly rather than through a lookup table: quantizing the linear
 * input first would collapse the bottom of the range (the first step of a
 * 1024-entry table already lands at ~12/255 after encoding), which is exactly
 * where the breathing and trail effects live. */
static inline uint8_t encode(float linear)
{
    if (linear <= 0.0f) {
        return 0;
    }
    if (linear >= 1.0f) {
        return 255;
    }
    return (uint8_t)lrintf(powf(linear, INV_GAMMA) * 255.0f);
}

/* Mirror of what the strip was last sent, for /frame. Physical order. */
static uint8_t  s_snapshot[RING_LEDS][4];
static uint32_t s_frames;

void output_show(const canvas_t *c, bool apply_brightness)
{
    const float k = apply_brightness ? s_brightness : 1.0f;

    for (int i = 0; i < RING_LEDS; i++) {
        const rgbw_t px = c->px[i];

        const uint8_t r = encode(px.r * k);
        const uint8_t g = encode(px.g * k);
        const uint8_t b = encode(px.b * k);
        const uint8_t w = encode(px.w * k);

        const int p = physical_index(i);
        led_strip_set_pixel_rgbw(s_strip, p, r, g, b, w);

        s_snapshot[p][0] = r;
        s_snapshot[p][1] = g;
        s_snapshot[p][2] = b;
        s_snapshot[p][3] = w;
    }
    led_strip_refresh(s_strip);
    s_frames++;
}

size_t output_snapshot(uint8_t *dst, size_t max_pixels)
{
    const size_t n = max_pixels < RING_LEDS ? max_pixels : RING_LEDS;
    memcpy(dst, s_snapshot, n * 4);
    return n;
}

uint32_t output_frame_count(void)
{
    return s_frames;
}

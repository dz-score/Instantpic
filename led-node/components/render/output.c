#include "output.h"

#include <math.h>

#include "esp_check.h"
#include "esp_log.h"
#include "led_strip.h"

static const char *TAG = "output";

#define INV_GAMMA (1.0f / 2.2f)

static led_strip_handle_t s_strip;
static float              s_brightness = CONFIG_LED_NODE_DEFAULT_BRIGHTNESS / 100.0f;

esp_err_t output_init(void)
{
    led_strip_config_t strip_config = {
        .strip_gpio_num   = CONFIG_LED_NODE_DATA_GPIO,
        .max_leds         = RING_LEDS,
        .led_pixel_format = LED_PIXEL_FORMAT_GRBW,
        .led_model        = LED_MODEL_SK6812,
        .flags.invert_out = false,
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
             CONFIG_LED_NODE_RING_REVERSED ? ", reversed" : "");
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
#if CONFIG_LED_NODE_RING_REVERSED
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

void output_show(const canvas_t *c, bool apply_brightness)
{
    const float k = apply_brightness ? s_brightness : 1.0f;

    for (int i = 0; i < RING_LEDS; i++) {
        const rgbw_t px = c->px[i];
        led_strip_set_pixel_rgbw(s_strip, physical_index(i),
                                 encode(px.r * k),
                                 encode(px.g * k),
                                 encode(px.b * k),
                                 encode(px.w * k));
    }
    led_strip_refresh(s_strip);
}

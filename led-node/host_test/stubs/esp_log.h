/* Logging is noise in a test run.
 *
 * The tag is still passed through, so the per-file `static const char *TAG` in
 * each module stays referenced — discarding it makes every module under test
 * emit an unused-variable warning. Arguments reach a variadic so -Wformat still
 * catches format-string mistakes.
 */
#pragma once

static inline void esp_log_noop(const char *tag, const char *fmt, ...)
{
    (void)tag;
    (void)fmt;
}

#define ESP_LOGE(tag, ...) esp_log_noop(tag, __VA_ARGS__)
#define ESP_LOGW(tag, ...) esp_log_noop(tag, __VA_ARGS__)
#define ESP_LOGI(tag, ...) esp_log_noop(tag, __VA_ARGS__)
#define ESP_LOGD(tag, ...) esp_log_noop(tag, __VA_ARGS__)
#define ESP_LOGV(tag, ...) esp_log_noop(tag, __VA_ARGS__)

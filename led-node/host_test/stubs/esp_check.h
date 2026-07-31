#pragma once

#include "esp_err.h"
#include "esp_log.h"

#define ESP_RETURN_ON_ERROR(x, tag, ...)          \
    do {                                          \
        esp_err_t err_rc_ = (x);                  \
        if (err_rc_ != ESP_OK) { return err_rc_; }\
    } while (0)

#define ESP_ERROR_CHECK(x) (void)(x)

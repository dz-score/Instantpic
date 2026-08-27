#pragma once

#include "freertos/FreeRTOS.h"

typedef void *TaskHandle_t;

BaseType_t xTaskCreatePinnedToCore(void (*fn)(void *), const char *name,
                                   uint32_t stack, void *arg, uint32_t prio,
                                   TaskHandle_t *out, int core);
BaseType_t xTaskCreate(void (*fn)(void *), const char *name, uint32_t stack,
                       void *arg, uint32_t prio, TaskHandle_t *out);
TickType_t xTaskGetTickCount(void);
void       vTaskDelayUntil(TickType_t *last, TickType_t period);
void       vTaskDelete(TaskHandle_t t);

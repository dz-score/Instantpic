/* Minimal FreeRTOS surface for the host build.
 *
 * Only what the code under test actually names. The point is to compile and
 * run the pure logic — the parser, the mode transitions, the canvas maths — not
 * to simulate a scheduler.
 */
#pragma once

#include <stdint.h>

typedef int      BaseType_t;
typedef uint32_t TickType_t;

#define pdTRUE  1
#define pdFALSE 0
#define pdPASS  1
#define pdFAIL  0

#define portMAX_DELAY 0xFFFFFFFFu

#define pdMS_TO_TICKS(ms) ((TickType_t)(ms))

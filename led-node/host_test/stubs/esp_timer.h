/* Controllable clock.
 *
 * modes.c derives every deadline from esp_timer_get_time(), so a settable fake
 * turns "wait 120 seconds for the printing timeout" into an assignment. Tests
 * that would otherwise be untestable — the 30 s capture release, the 10 s link
 * watchdog — become instant and deterministic.
 */
#pragma once

#include <stdint.h>

int64_t esp_timer_get_time(void);

/* Host-only: move the fake clock. Not part of the ESP-IDF API. */
void test_clock_set_ms(int64_t ms);
void test_clock_advance_ms(int64_t ms);

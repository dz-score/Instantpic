/* wifi_sta.h — station-mode WiFi for the development transport.
 *
 * Only the HTTP transport needs a network; the booth build has none at all.
 * So connectivity is owned by the transport that requires it rather than by
 * main, and it compiles out with the rest of the HTTP build.
 *
 * Replaces example_connect() from protocol_examples_common, which tied the
 * build to Espressif's examples tree and named our config keys EXAMPLE_*.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Starts WiFi and returns immediately — it does NOT wait for an association.
 *
 * Nothing needs to block on this: the render task is already running (so the
 * ring animates through the whole connection attempt), and the HTTP server
 * binds to any address, so it is ready the moment DHCP lands. If the hotspot
 * is not up yet, this retries forever rather than failing. */
esp_err_t wifi_sta_start(void);

bool wifi_sta_is_connected(void);

/* Writes the current IPv4 address, or "0.0.0.0" when not connected. */
void wifi_sta_ip_str(char *buf, size_t len);

#ifdef __cplusplus
}
#endif

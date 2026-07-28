#include "sdkconfig.h"

#if CONFIG_LED_NODE_TRANSPORT_HTTP

#include "wifi_sta.h"

#include <stdio.h>
#include <string.h>

#include "esp_check.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"

static const char *TAG = "wifi";

static esp_netif_t *s_netif;
static bool         s_connected;
static uint32_t     s_attempts;

static void on_wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg;
    (void)base;

    if (id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
        return;
    }

    if (id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *e = (const wifi_event_sta_disconnected_t *)data;
        s_connected = false;

        /* Retry forever. The hotspot may simply not be up yet, and a node that
         * gave up would need a power cycle to notice it appeared. Log the first
         * failure and then every tenth, so a missing AP does not flood the
         * console for the whole session. */
        if (s_attempts++ % 10 == 0) {
            ESP_LOGW(TAG, "not connected (reason %d), retrying", e->reason);
        }
        esp_wifi_connect();
    }
}

static void on_got_ip(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg;
    (void)base;
    (void)id;

    const ip_event_got_ip_t *e = (const ip_event_got_ip_t *)data;
    s_connected = true;
    s_attempts  = 0;
    ESP_LOGI(TAG, "connected — open http://" IPSTR "/", IP2STR(&e->ip_info.ip));
}

esp_err_t wifi_sta_start(void)
{
    ESP_RETURN_ON_ERROR(esp_netif_init(), TAG, "esp_netif_init failed");
    ESP_RETURN_ON_ERROR(esp_event_loop_create_default(), TAG, "event loop failed");

    s_netif = esp_netif_create_default_wifi_sta();
    ESP_RETURN_ON_FALSE(s_netif != NULL, ESP_FAIL, TAG, "netif create failed");

    wifi_init_config_t init_cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_RETURN_ON_ERROR(esp_wifi_init(&init_cfg), TAG, "esp_wifi_init failed");

    ESP_RETURN_ON_ERROR(esp_event_handler_instance_register(
                            WIFI_EVENT, ESP_EVENT_ANY_ID, &on_wifi_event, NULL, NULL),
                        TAG, "wifi handler failed");
    ESP_RETURN_ON_ERROR(esp_event_handler_instance_register(
                            IP_EVENT, IP_EVENT_STA_GOT_IP, &on_got_ip, NULL, NULL),
                        TAG, "ip handler failed");

    wifi_config_t cfg = {0};
    strlcpy((char *)cfg.sta.ssid, CONFIG_LED_NODE_WIFI_SSID, sizeof(cfg.sta.ssid));
    strlcpy((char *)cfg.sta.password, CONFIG_LED_NODE_WIFI_PASSWORD, sizeof(cfg.sta.password));
    /* threshold.authmode is left at WIFI_AUTH_OPEN so we accept whatever the
     * hotspot offers — PC and phone hotspots vary, and a strict threshold fails
     * in a way that looks exactly like a wrong password. */

    ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_STA), TAG, "set_mode failed");
    ESP_RETURN_ON_ERROR(esp_wifi_set_config(WIFI_IF_STA, &cfg), TAG, "set_config failed");
    ESP_RETURN_ON_ERROR(esp_wifi_start(), TAG, "esp_wifi_start failed");

    /* Modem sleep adds tens to hundreds of milliseconds to inbound packets,
     * which would make command latency during development unrepresentative of
     * the wired transport this eventually becomes. */
    ESP_RETURN_ON_ERROR(esp_wifi_set_ps(WIFI_PS_NONE), TAG, "set_ps failed");

    ESP_LOGI(TAG, "connecting to \"%s\" (2.4 GHz only)", CONFIG_LED_NODE_WIFI_SSID);
    return ESP_OK;
}

bool wifi_sta_is_connected(void)
{
    return s_connected;
}

void wifi_sta_ip_str(char *buf, size_t len)
{
    esp_netif_ip_info_t ip = {0};

    if (s_netif != NULL && esp_netif_get_ip_info(s_netif, &ip) == ESP_OK) {
        snprintf(buf, len, IPSTR, IP2STR(&ip.ip));
    } else {
        strlcpy(buf, "0.0.0.0", len);
    }
}

#else /* !CONFIG_LED_NODE_TRANSPORT_HTTP */

typedef int wifi_sta_not_selected_t;

#endif

/* led-node — photobooth LED ring controller.
 *
 * Behavior spec:  Docs/LED_SPEC.md
 * Design:         Docs/LED_NODE_ARCHITECTURE.md
 *
 * This file does nothing but wire the pieces together: create the command
 * queue, start the render task, start whichever transport is compiled in.
 *
 * Note there is no network setup here. Only the HTTP transport needs a
 * network, so it owns bringing one up; the booth build has none at all.
 */
#include "esp_err.h"
#include "esp_log.h"
#include "nvs_flash.h"

#include "command.h"
#include "modes.h"
#include "transport.h"

static const char *TAG = "led_node";

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    QueueHandle_t cmd_q = xQueueCreate(MODE_CMD_QUEUE_DEPTH, sizeof(cmd_req_t));
    ESP_ERROR_CHECK(cmd_q != NULL ? ESP_OK : ESP_ERR_NO_MEM);

    /* Render first, so the boot self-test and the waiting dot are on screen
     * while the transport (and, in dev builds, WiFi) is still coming up. The
     * node has to look correct with no host talking to it at all. */
    ESP_ERROR_CHECK(modes_start(cmd_q));

    ESP_ERROR_CHECK(transport_start(cmd_q));

    ESP_LOGI(TAG, "ready");
}

/* led-node — photobooth LED ring controller.
 *
 * Behavior spec:  Docs/LED_SPEC.md
 * Design:         Docs/LED_NODE_ARCHITECTURE.md
 *
 * This file does nothing but wire the pieces together: create the command
 * queue, start the render task, start whichever transport is compiled in.
 */
#include "esp_err.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "sdkconfig.h"

#include "command.h"
#include "modes.h"
#include "transport.h"

#if CONFIG_LED_NODE_TRANSPORT_HTTP
#include "esp_event.h"
#include "esp_netif.h"
#include "protocol_examples_common.h"
#endif

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
     * while the network (in dev builds) is still coming up. The node has to
     * look correct with no host talking to it at all. */
    ESP_ERROR_CHECK(modes_start(cmd_q));

#if CONFIG_LED_NODE_TRANSPORT_HTTP
    /* Development transport. example_connect() pulls credentials from
     * menuconfig; it is compiled out entirely of the booth build. */
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    ESP_ERROR_CHECK(example_connect());
#endif

    ESP_ERROR_CHECK(transport_start(cmd_q));

    ESP_LOGI(TAG, "ready");
}

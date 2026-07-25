#include "transport.h"

#include <string.h>

#include "esp_log.h"

static const char *TAG = "transport";

/* Only one transport is compiled in, and each has a single intake task, so one
 * reply queue suffices. */
static QueueHandle_t s_reply_q;

esp_err_t transport_common_init(void)
{
    if (s_reply_q != NULL) {
        return ESP_OK;
    }
    s_reply_q = xQueueCreate(1, sizeof(cmd_reply_t));
    if (s_reply_q == NULL) {
        ESP_LOGE(TAG, "reply queue alloc failed");
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

void transport_submit(QueueHandle_t cmd_q, const char *line, size_t len,
                      char *reply, size_t reply_sz)
{
    command_t cmd;

    if (!command_parse(line, len, &cmd)) {
        /* Unknown verbs are reported but never fault the node — the firmware
         * and the backend version independently, and drift has to degrade
         * gracefully. */
        strlcpy(reply, "ERR UNKNOWN", reply_sz);
        return;
    }

    cmd_req_t req = {
        .cmd     = cmd,
        .reply_q = s_reply_q,
    };

    if (xQueueSend(cmd_q, &req, pdMS_TO_TICKS(TRANSPORT_REPLY_TIMEOUT_MS)) != pdTRUE) {
        ESP_LOGW(TAG, "command queue full, dropped %s", command_verb_name(cmd.verb));
        strlcpy(reply, "ERR BUSY", reply_sz);
        return;
    }

    cmd_reply_t rep;
    if (xQueueReceive(s_reply_q, &rep, pdMS_TO_TICKS(TRANSPORT_REPLY_TIMEOUT_MS)) != pdTRUE) {
        /* The render task did not answer in time. Report it rather than
         * pretending success: CAPTURE gates the shutter on this reply. */
        ESP_LOGW(TAG, "no reply for %s", command_verb_name(cmd.verb));
        strlcpy(reply, "ERR TIMEOUT", reply_sz);
        return;
    }

    strlcpy(reply, rep.text, reply_sz);
}

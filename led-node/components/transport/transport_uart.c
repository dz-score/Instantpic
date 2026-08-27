/* UART transport — the booth wire.
 *
 * Same parser and same vocabulary as the HTTP transport. Lines in, replies out,
 * one per line.
 *
 * NOTE: with CONFIG_LED_NODE_UART_PORT=0 this shares the port the console logs
 * to. Set CONFIG_ESP_CONSOLE_NONE for booth builds, or ESP_LOG output will be
 * interleaved into the protocol stream the Pi is parsing.
 */
#include "sdkconfig.h"

#if CONFIG_LED_NODE_TRANSPORT_UART

#include "transport.h"

#include <stdbool.h>
#include <string.h>

#include "driver/uart.h"
#include "esp_check.h"
#include "esp_log.h"
#include "freertos/task.h"

static const char *TAG = "transport_uart";

#define UART_PORT     CONFIG_LED_NODE_UART_PORT
#define UART_BAUD     CONFIG_LED_NODE_UART_BAUD
#define UART_RX_BUF   512
#define UART_TASK_PRIO 5

static QueueHandle_t s_cmd_q;
static TaskHandle_t  s_task;

static void write_reply(const char *reply)
{
    uart_write_bytes(UART_PORT, reply, strlen(reply));
    uart_write_bytes(UART_PORT, "\n", 1);
}

static void uart_task(void *arg)
{
    (void)arg;

    char    line[CMD_LINE_MAX];
    size_t  len        = 0;
    bool    discarding = false;
    char    reply[CMD_REPLY_MAX];
    uint8_t byte;

    while (true) {
        const int n = uart_read_bytes(UART_PORT, &byte, 1, portMAX_DELAY);
        if (n != 1) {
            continue;
        }

        if (byte == '\n') {
            if (discarding) {
                write_reply("ERR TOOLONG");
            } else if (len > 0) {
                transport_submit(s_cmd_q, line, len, reply, sizeof(reply));
                write_reply(reply);
            }
            len        = 0;
            discarding = false;
            continue;
        }

        if (len < sizeof(line)) {
            line[len++] = (char)byte;
        } else {
            /* Overlong line: discard through to the next newline rather than
             * emitting a command parsed from a truncated one. */
            discarding = true;
        }
    }
}

esp_err_t transport_start(QueueHandle_t cmd_q)
{
    esp_err_t err = transport_common_init();
    if (err != ESP_OK) {
        return err;
    }
    s_cmd_q = cmd_q;

    const uart_config_t cfg = {
        .baud_rate  = UART_BAUD,
        .data_bits  = UART_DATA_8_BITS,
        .parity     = UART_PARITY_DISABLE,
        .stop_bits  = UART_STOP_BITS_1,
        .flow_ctrl  = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };

    ESP_RETURN_ON_ERROR(uart_driver_install(UART_PORT, UART_RX_BUF, 0, 0, NULL, 0),
                        TAG, "uart_driver_install failed");
    ESP_RETURN_ON_ERROR(uart_param_config(UART_PORT, &cfg), TAG, "uart_param_config failed");

    if (xTaskCreate(uart_task, "uart_rx", 3072, NULL, UART_TASK_PRIO, &s_task) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }

    ESP_LOGI(TAG, "listening on UART%d @ %d baud", UART_PORT, UART_BAUD);
    return ESP_OK;
}

void transport_stop(void)
{
    if (s_task != NULL) {
        vTaskDelete(s_task);
        s_task = NULL;
    }
    uart_driver_delete(UART_PORT);
}

#else  /* !CONFIG_LED_NODE_TRANSPORT_UART */

typedef int transport_uart_not_selected_t;

#endif /* CONFIG_LED_NODE_TRANSPORT_UART */

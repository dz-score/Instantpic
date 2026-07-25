/* HTTP transport — development only.
 *
 * There is deliberately no REST surface: no /capture, no /countdown, no /idle.
 * A single /cmd endpoint carries the identical ASCII line the UART carries, so
 * the two transports cannot drift apart and the eventual swap is provably
 * behavior-identical.
 */
#include "sdkconfig.h"

#if CONFIG_LED_NODE_TRANSPORT_HTTP

#include "transport.h"

#include <ctype.h>
#include <stdlib.h>
#include <string.h>

#include "esp_http_server.h"
#include "esp_log.h"

static const char *TAG = "transport_http";

static httpd_handle_t s_server;
static QueueHandle_t  s_cmd_q;

/* Minimal percent-decode, in place, so a browser address bar works:
 * "PHASE%20280" and "PHASE+280" both arrive as "PHASE 280". */
static size_t uri_decode(char *s)
{
    char  *out = s;
    size_t n   = 0;

    for (char *in = s; *in != '\0'; in++) {
        if (*in == '+') {
            *out++ = ' ';
        } else if (*in == '%' && isxdigit((unsigned char)in[1]) && isxdigit((unsigned char)in[2])) {
            char hex[3] = {in[1], in[2], '\0'};
            *out++      = (char)strtol(hex, NULL, 16);
            in += 2;
        } else {
            *out++ = *in;
        }
        n++;
    }
    *out = '\0';
    return n;
}

static void respond(httpd_req_t *req, const char *reply)
{
    httpd_resp_set_type(req, "text/plain");
    /* ERR replies are still HTTP 200: the request was well-formed, the command
     * was not. Curl scripts read the body, not the status. */
    httpd_resp_sendstr(req, reply);
}

/* GET /cmd?c=CAPTURE — typeable in a browser bar, handy during setup. */
static esp_err_t cmd_get_handler(httpd_req_t *req)
{
    char query[CMD_LINE_MAX * 3];
    char line[CMD_LINE_MAX];
    char reply[CMD_REPLY_MAX];

    if (httpd_req_get_url_query_str(req, query, sizeof(query)) != ESP_OK ||
        httpd_query_key_value(query, "c", line, sizeof(line)) != ESP_OK) {
        respond(req, "ERR NOCMD");
        return ESP_OK;
    }

    const size_t len = uri_decode(line);
    transport_submit(s_cmd_q, line, len, reply, sizeof(reply));
    respond(req, reply);
    return ESP_OK;
}

/* POST /cmd with the raw line as the body. */
static esp_err_t cmd_post_handler(httpd_req_t *req)
{
    char line[CMD_LINE_MAX];
    char reply[CMD_REPLY_MAX];

    if (req->content_len >= sizeof(line)) {
        respond(req, "ERR TOOLONG");
        return ESP_OK;
    }

    int received = httpd_req_recv(req, line, req->content_len);
    if (received <= 0) {
        respond(req, "ERR RECV");
        return ESP_OK;
    }
    line[received] = '\0';

    transport_submit(s_cmd_q, line, (size_t)received, reply, sizeof(reply));
    respond(req, reply);
    return ESP_OK;
}

/* Dev-only console. Lives entirely inside this transport and leaks nothing
 * upward — it disappears with the HTTP build. */
static const char DEV_PAGE[] =
    "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
    "<title>LED node</title>"
    "<style>body{font:16px system-ui;margin:2rem;max-width:30rem}"
    "button{font:inherit;padding:.6rem 1rem;margin:.2rem;min-width:8rem}"
    "#out{font-family:monospace;margin-top:1rem;padding:.5rem;background:#eee}</style>"
    "<h1>LED node</h1><div id=b></div><div id=out>ready</div>"
    "<script>"
    "const cmds=['IDLE','PHASE 280','COUNTDOWN 3000','CAPTURE','RELEASE',"
    "'PRINTING','FINISHED 4000','ERROR 1','PING'];"
    "const b=document.getElementById('b'),o=document.getElementById('out');"
    "cmds.forEach(c=>{const e=document.createElement('button');e.textContent=c;"
    "e.onclick=async()=>{o.textContent=await(await fetch('/cmd?c='+encodeURIComponent(c))).text()};"
    "b.appendChild(e)});"
    "</script>";

static esp_err_t root_get_handler(httpd_req_t *req)
{
    httpd_resp_set_type(req, "text/html");
    httpd_resp_send(req, DEV_PAGE, HTTPD_RESP_USE_STRLEN);
    return ESP_OK;
}

static const httpd_uri_t uri_cmd_get  = {.uri = "/cmd", .method = HTTP_GET,  .handler = cmd_get_handler};
static const httpd_uri_t uri_cmd_post = {.uri = "/cmd", .method = HTTP_POST, .handler = cmd_post_handler};
static const httpd_uri_t uri_root     = {.uri = "/",    .method = HTTP_GET,  .handler = root_get_handler};

esp_err_t transport_start(QueueHandle_t cmd_q)
{
    esp_err_t err = transport_common_init();
    if (err != ESP_OK) {
        return err;
    }
    s_cmd_q = cmd_q;

    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.lru_purge_enable = true;

    err = httpd_start(&s_server, &config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "httpd_start failed: %s", esp_err_to_name(err));
        return err;
    }

    httpd_register_uri_handler(s_server, &uri_cmd_get);
    httpd_register_uri_handler(s_server, &uri_cmd_post);
    httpd_register_uri_handler(s_server, &uri_root);

    ESP_LOGI(TAG, "listening on port %d", config.server_port);
    return ESP_OK;
}

void transport_stop(void)
{
    if (s_server != NULL) {
        httpd_stop(s_server);
        s_server = NULL;
    }
}

#else  /* !CONFIG_LED_NODE_TRANSPORT_HTTP */

/* Both transports are always compiled; the unselected one guards out to
 * nothing. Keep the translation unit non-empty. */
typedef int transport_http_not_selected_t;

#endif /* CONFIG_LED_NODE_TRANSPORT_HTTP */

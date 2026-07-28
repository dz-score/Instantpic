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

#include "canvas.h"
#include "modes.h"
#include "output.h"
#include "wifi_sta.h"

static const char *TAG = "transport_http";

static httpd_handle_t s_server;
static QueueHandle_t  s_cmd_q;

/* An unset Kconfig bool emits no #define at all, so it cannot appear in a
 * runtime expression. Same normalization as output.c. */
#if CONFIG_LED_NODE_RING_REVERSED
#define RING_REVERSED 1
#else
#define RING_REVERSED 0
#endif

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

/* --- introspection endpoints ---------------------------------------------
 *
 * Read-only. Commands still only ever reach the render task through the queue;
 * these just look at what it did. Dev build only — they vanish with the HTTP
 * transport.
 */

/* Big enough for 60 pixels x 4 channels as decimal, plus the header fields.
 * Static rather than stack: the httpd task's stack is not the place for 1.2 kB.
 * Safe because esp_http_server serves requests from a single task. */
static char s_json[1600];

/* GET /frame — the last frame pushed to the strip, in physical pixel order.
 * This is the real render pipeline output: canvas, brightness, geometry and
 * gamma have all already been applied. */
static esp_err_t frame_get_handler(httpd_req_t *req)
{
    static uint8_t px[RING_LEDS][4];
    const size_t   n = output_snapshot((uint8_t *)px, RING_LEDS);

    modes_state_t st;
    modes_get_state(&st);

    int len = snprintf(s_json, sizeof(s_json),
                       "{\"frame\":%lu,\"mode\":\"%s\",\"elapsed_ms\":%lu,\"n\":%u,\"px\":[",
                       (unsigned long)output_frame_count(), modes_mode_name(st.mode),
                       (unsigned long)st.elapsed_ms, (unsigned)n);

    for (size_t i = 0; i < n && len > 0 && len < (int)sizeof(s_json); i++) {
        len += snprintf(s_json + len, sizeof(s_json) - len, "%s%u,%u,%u,%u",
                        i ? "," : "", px[i][0], px[i][1], px[i][2], px[i][3]);
    }
    if (len > 0 && len < (int)sizeof(s_json)) {
        len += snprintf(s_json + len, sizeof(s_json) - len, "]}");
    }

    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr(req, s_json);
    return ESP_OK;
}

/* GET /state — the mode machine's view of itself. */
static esp_err_t state_get_handler(httpd_req_t *req)
{
    modes_state_t st;
    modes_get_state(&st);

    char ip[16];
    wifi_sta_ip_str(ip, sizeof(ip));

    snprintf(s_json, sizeof(s_json),
             "{\"ip\":\"%s\","
             "\"mode\":\"%s\",\"elapsed_ms\":%lu,\"since_rx_ms\":%lu,"
             "\"brightness\":%.2f,\"hue\":%.1f,\"duration_ms\":%lu,\"code\":%ld,"
             "\"frames\":%lu,\"leds\":%d,\"offset\":%d,\"reversed\":%s,"
             "\"link_timeout_ms\":%d}",
             ip, modes_mode_name(st.mode), (unsigned long)st.elapsed_ms,
             (unsigned long)st.since_rx_ms, output_get_brightness(), st.hue,
             (unsigned long)st.duration_ms, (long)st.code,
             (unsigned long)output_frame_count(), RING_LEDS,
             CONFIG_LED_NODE_RING_OFFSET, RING_REVERSED ? "true" : "false",
             MODE_LINK_TIMEOUT_MS);

    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr(req, s_json);
    return ESP_OK;
}

/* Dev-only console. Lives entirely inside this transport and leaks nothing
 * upward — it disappears with the HTTP build. */
static const char DEV_PAGE[] =
    "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
    "<title>LED node</title>"
    "<style>"
    "body{font:15px system-ui;margin:0;padding:1.5rem;background:#14161a;color:#e8e8ea;"
    "display:flex;flex-wrap:wrap;gap:2rem;justify-content:center}"
    "#ringwrap{position:relative;flex:0 0 auto}"
    "svg{display:block;background:#0b0c0e;border-radius:50%;box-shadow:0 0 60px #0006 inset}"
    "#panel{flex:1 1 16rem;max-width:22rem}"
    "h1{font-size:1rem;letter-spacing:.08em;text-transform:uppercase;color:#8a8f98;margin:0 0 1rem}"
    "button{font:inherit;padding:.5rem .8rem;margin:0 .3rem .3rem 0;border:1px solid #2c3038;"
    "background:#1c1f25;color:#e8e8ea;border-radius:6px;cursor:pointer}"
    "button:hover{background:#252932;border-color:#3d434e}"
    "#out,#st{font-family:ui-monospace,monospace;font-size:.8rem;margin-top:.8rem;padding:.5rem .6rem;"
    "background:#0b0c0e;border-radius:6px;color:#9aa3b0;white-space:pre-wrap;word-break:break-all}"
    "#mode{font-size:1.5rem;font-weight:600;margin:.4rem 0 0}"
    "#hbl{display:block;margin-top:.7rem;color:#9aa3b0;font-size:.85rem}"
    "#hbl small{color:#6b7280}"
    "</style>"
    "<div id=ringwrap><svg id=ring width=340 height=340 viewBox='0 0 340 340'></svg></div>"
    "<div id=panel><h1>LED node</h1><div id=mode>-</div>"
    "<div id=b></div>"
    "<label id=hbl><input type=checkbox id=hb checked> heartbeat &mdash; PING every 2s"
    "<br><small>uncheck to watch the link watchdog fire</small></label>"
    "<div id=out>ready</div><div id=st>-</div></div>"
    "<script>"
    "const N=60,R=132,C=170;"
    "const svg=document.getElementById('ring'),NS='http://www.w3.org/2000/svg',dots=[],glow=[];"
    "for(let i=0;i<N;i++){"
    "const a=(i*360/N-90)*Math.PI/180,x=C+R*Math.cos(a),y=C+R*Math.sin(a);"
    "const g=document.createElementNS(NS,'circle');"
    "g.setAttribute('cx',x);g.setAttribute('cy',y);g.setAttribute('r',13);"
    "g.setAttribute('fill','#000');g.setAttribute('opacity','0.35');"
    "svg.appendChild(g);glow.push(g);}"
    "for(let i=0;i<N;i++){"
    "const a=(i*360/N-90)*Math.PI/180,x=C+R*Math.cos(a),y=C+R*Math.sin(a);"
    "const c=document.createElementNS(NS,'circle');"
    "c.setAttribute('cx',x);c.setAttribute('cy',y);c.setAttribute('r',5);"
    "c.setAttribute('fill','#000');svg.appendChild(c);dots.push(c);}"
    "const out=document.getElementById('out'),stEl=document.getElementById('st'),"
    "modeEl=document.getElementById('mode');"
    "const cmds=['IDLE','PHASE 280','COUNTDOWN 3000','CAPTURE','RELEASE',"
    "'PRINTING','FINISHED 4000','ERROR 1','PING'];"
    "const bEl=document.getElementById('b');"
    "cmds.forEach(c=>{const e=document.createElement('button');e.textContent=c;"
    "e.onclick=async()=>{out.textContent=await(await fetch('/cmd?c='+encodeURIComponent(c))).text()};"
    "bEl.appendChild(e)});"
    /* RGBW -> screen. The white die and the colour dies sit in one package and
       the eye sums them, so approximate that by adding W into each channel. */
    "function css(r,g,b,w){return 'rgb('+Math.min(255,r+w)+','+Math.min(255,g+w)+','"
    "+Math.min(255,b+w)+')';}"
    "let miss=0;"
    "async function tick(){"
    "try{const f=await(await fetch('/frame')).json();"
    "for(let i=0;i<f.n;i++){const o=i*4,c=css(f.px[o],f.px[o+1],f.px[o+2],f.px[o+3]);"
    "dots[i].setAttribute('fill',c);glow[i].setAttribute('fill',c);}"
    "modeEl.textContent=f.mode;miss=0;}catch(e){if(++miss>3)modeEl.textContent='(offline)';}"
    "setTimeout(tick,50);}"
    "async function poll(){"
    "try{const s=await(await fetch('/state')).json();"
    "stEl.textContent=JSON.stringify(s,null,1);}catch(e){}"
    "setTimeout(poll,700);}"
    /* The Pi will do exactly this. Without it the watchdog trips after 10s of
       just watching, which is correct but makes the preview useless. */
    "setInterval(()=>{if(document.getElementById('hb').checked)"
    "fetch('/cmd?c=PING').catch(()=>{})},2000);"
    "tick();poll();"
    "</script>";

static esp_err_t root_get_handler(httpd_req_t *req)
{
    httpd_resp_set_type(req, "text/html");
    httpd_resp_send(req, DEV_PAGE, HTTPD_RESP_USE_STRLEN);
    return ESP_OK;
}

static const httpd_uri_t uri_cmd_get  = {.uri = "/cmd",   .method = HTTP_GET,  .handler = cmd_get_handler};
static const httpd_uri_t uri_cmd_post = {.uri = "/cmd",   .method = HTTP_POST, .handler = cmd_post_handler};
static const httpd_uri_t uri_frame    = {.uri = "/frame", .method = HTTP_GET,  .handler = frame_get_handler};
static const httpd_uri_t uri_state    = {.uri = "/state", .method = HTTP_GET,  .handler = state_get_handler};
static const httpd_uri_t uri_root     = {.uri = "/",      .method = HTTP_GET,  .handler = root_get_handler};

esp_err_t transport_start(QueueHandle_t cmd_q)
{
    esp_err_t err = transport_common_init();
    if (err != ESP_OK) {
        return err;
    }
    s_cmd_q = cmd_q;

    /* Non-blocking: httpd binds to any address, so it is ready before the
     * association completes and stays correct across reconnects. */
    err = wifi_sta_start();
    if (err != ESP_OK) {
        return err;
    }

    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.lru_purge_enable = true;
    /* The preview page polls /frame at ~20 Hz and keeps a connection warm
     * alongside command requests. */
    config.stack_size      = 5120;
    config.max_open_sockets = 7;

    err = httpd_start(&s_server, &config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "httpd_start failed: %s", esp_err_to_name(err));
        return err;
    }

    httpd_register_uri_handler(s_server, &uri_cmd_get);
    httpd_register_uri_handler(s_server, &uri_cmd_post);
    httpd_register_uri_handler(s_server, &uri_frame);
    httpd_register_uri_handler(s_server, &uri_state);
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

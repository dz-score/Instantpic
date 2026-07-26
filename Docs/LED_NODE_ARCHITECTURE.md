# LED_NODE_ARCHITECTURE — ESP32 Firmware Design

How `led-node/` is structured to deliver the behavior in
[LED_SPEC.md](LED_SPEC.md). That document says *what the ring does*; this one
says *how the firmware is arranged to do it*.

**Status:** design, written 2026-07-25. `led-node/` is currently a verbatim copy
of the ESP-IDF `examples/protocols/http_server/simple` example with no LED code
in it — see [Scaffold remediation](#scaffold-remediation) for what has to go.

---

## Platform

- **ESP-IDF, C.** Not Arduino. The existing scaffold is an IDF project
  (`CMakeLists.txt` + `idf_component.yml` + `Kconfig.projbuild`).
- **LED driver: `espressif/led_strip`** from the component registry, RMT
  backend, `LED_PIXEL_FORMAT_GRBW`. It exposes `led_strip_set_pixel_rgbw()`,
  which is what Capture's W-channel requirement needs.
- **RMT, specifically.** RMT output is hardware-timed and DMA-fed, so it is
  unbothered by interrupt latency spikes from the WiFi stack. A bit-banged
  driver would visibly glitch under the dev build. This is what makes it safe to
  develop over WiFi and ship over serial without the rendering behavior changing.

---

## Concurrency: why there is a command queue

`esp_http_server` is not a poll-from-your-loop server. `httpd_start()` spawns its
own task, and URI handlers execute on it. Concurrency is not optional here — the
HTTP handler and the render loop are on different tasks whether or not the design
acknowledges it.

Rather than put a mutex around mode state, **transports never touch mode state at
all.** They produce commands onto a queue and wait for a reply:

```c
typedef struct {
    command_t     cmd;
    QueueHandle_t reply_q;   // where the render task posts the result
} cmd_req_t;
```

The render task is the sole owner of mode state, so there is no lock anywhere in
the system.

This is also what makes the dev→booth transport swap safe: the queue *is* the
seam. Nothing above it can tell which transport is running.

## Task model

| Task | Core | Prio | Role |
|---|---|---|---|
| `render_task` | 1 | 6 | Owns mode state + framebuffer. Drains queue, renders, refreshes strip. |
| `httpd` (dev build) | any | 5 | Parses `/cmd`, enqueues, blocks on reply, responds. |
| `uart_task` (booth build) | any | 5 | Blocks on `uart_read_bytes`, parses, enqueues, writes reply line. |

**Frame budget.** 120 Hz is 8.33 ms. `led_strip_refresh()` for 60 RGBW pixels is
~2.4 ms, leaving ~6 ms. Cadence comes from `vTaskDelayUntil`, which both holds a
fixed rate and yields cleanly.

**Pinning.** ESP-IDF defaults the WiFi task to core 0, so pinning the render task
to core 1 isolates it during the dev build. On a single-core part (C3, C6) that
option doesn't exist and the priority ordering is what protects the frame rate
instead.

**Latency.** The render task drains the queue at frame start, so a command waits
at most one frame (~8 ms) before it is applied. Transports use a 200 ms reply
timeout, which is an error detector rather than an expected wait.

## Layer stack

```
transport (http | uart)     ── parses, enqueues, awaits reply
        │  cmd_req_t queue
        ▼
mode manager                ── current mode + params + entry time,
        │                      transitions, timeouts, link watchdog
        ▼
animations                  ── render(elapsed_ms, params, *canvas), one per mode
        │
        ▼
compositor                  ── cross-fade: render two modes, blend in linear
        │
        ▼
output                      ── global brightness → gamma LUT → geometry → strip
```

---

## The three decisions that shape the code

### 1. Animations are pure functions of elapsed time

`render(elapsed_ms, params, canvas)` — no internal state, no accumulators.

This is not stylistic. The 200 ms cross-fade in the spec requires rendering the
outgoing *and* incoming mode within the same frame, which is only possible if a
mode can be evaluated at an arbitrary `t` without having been "running." It also
eliminates drift and makes every animation testable by evaluating it at a
timestamp.

The one exception is Finished's sparkles, which need randomness. Seed a PRNG
deterministically from mode-entry time and frame index so it remains a pure
function of `t`.

### 2. Blend in linear space; gamma is the last step

The canvas holds **linear** RGBW (uint16 or float). Cross-fades, brightness, and
compositing all happen in linear space. The gamma LUT (γ ≈ 2.2) is applied once,
immediately before handing bytes to `led_strip`.

Cross-fading two gamma-encoded buffers produces a visible dip through the
midpoint — the fade appears to duck dark halfway. Getting this order wrong is the
usual reason a project has a gamma table and *still* looks cheap.

### 3. Sub-pixel rendering lives in the primitives

Two drawing functions:

```c
void canvas_point(canvas_t *c, float degrees, rgbw_t color, float falloff);
void canvas_arc  (canvas_t *c, float deg_start, float deg_end, rgbw_t color);
```

Both anti-alias across pixel boundaries. Every animation is built from them, so
no animation *can* forget to sub-pixel, and `RING_OFFSET` / `RING_DIRECTION` are
applied in exactly one place.

**Animations work in degrees and never see a pixel index.** That is what lets the
physical seam land wherever mounting is convenient.

---

## Transport abstraction

```c
typedef struct {
    esp_err_t (*start)(QueueHandle_t cmd_q);
    void      (*stop)(void);
} transport_t;
```

Exactly one is compiled in, selected by Kconfig. Both do the same three things:
parse a line with the shared parser, enqueue it, wait for the reply and return it
to the caller.

### Development strategy

The booth's USB port is occupied by the Pi, which makes flashing from a dev
machine impossible while serial is wired. So:

1. **Build and iterate on the HTTP transport.** USB stays free for flashing;
   commands come from `curl` or a browser.
2. **Switch to the UART transport for the booth.** WiFi is compiled out entirely.

This is a dev-ergonomics decision, not an architectural one — the reasoning in
[LED_SPEC.md](LED_SPEC.md) for why the *production* transport is serial is
unchanged.

**One parser, one vocabulary.** There is no REST surface — no `/capture`,
`/countdown`, `/idle`. A single `/cmd` endpoint carries the identical ASCII line
the UART carries:

```
POST /cmd      body: COUNTDOWN 3000
GET  /cmd?c=CAPTURE          ← typeable in a browser, handy during setup
```

Two vocabularies would drift, and the drift would surface as a behavior
difference on the night the transport changes. One parser makes the swap
provably behavior-identical.

A dev-only HTML page with a button per mode may hang off `GET /`. It lives
entirely inside the HTTP transport and leaks nothing upward.

### Swap checklist

- Bring up serial **well before the event.** The firmware change is small; the
  surroundings are not — a udev rule pinning the ESP32's USB serial number to a
  stable `/dev/led-node`, baud, buffer sizes, and the Pi-side client.
- Tune the Capture ack timeout **on serial**. The latency distributions aren't
  comparable, and that timeout is what stands between a hiccup and a dark photo.
- Keep `PING`/`PONG` in both builds, or the link watchdog's first real test is
  in production.

---

## Wire protocol

ASCII, newline-terminated, human-typeable on purpose — the whole booth can be
driven from a serial monitor at the venue with no tooling.

```
IDLE
PHASE <hue>
COUNTDOWN <ms>
CAPTURE              → OK CAPTURE      (Pi waits for this, then fires shutter)
RELEASE
PRINTING
FINISHED <ms>
ERROR <code>
PING                 → PONG
```

Replies are `OK <verb>` or `ERR <reason>`. Unknown verbs return `ERR UNKNOWN` and
are otherwise ignored — never a fault state, so the two artifacts can version
independently.

`PING` is not debug sugar. The link watchdog measures time since *any* received
line; without a heartbeat, a booth sitting in Idle for an hour would trip into
Link Lost while nothing is wrong. The Pi sends `PING` every ~2 s.

---

## Component layout

```
led-node/
  CMakeLists.txt              project(led_node)
  sdkconfig.defaults
  main/
    CMakeLists.txt
    Kconfig.projbuild         ring size, data GPIO, RING_OFFSET, direction, wifi
    main.c                    init + task creation only
  components/
    protocol/                 command_t, the one parser  ← host-testable
    transport/                transport.h, transport_http.c, transport_uart.c
    render/
      canvas.[ch]             linear framebuffer, canvas_point/canvas_arc
      output.[ch]             brightness, gamma LUT, geometry, led_strip push
      modes.[ch]              mode manager, transitions, timeouts, watchdog
      anim_idle.c
      anim_playful.c
      anim_countdown.c
      anim_capture.c
      anim_printing.c
      anim_finished.c
      anim_error.c
      anim_boot.c
      anim_linklost.c
```

Parsing lives in its own component rather than in either transport — that is what
structurally prevents a second vocabulary from appearing.

## Build configuration

Transport is a Kconfig `choice`, with `main/CMakeLists.txt` selecting sources so
the booth binary does not contain the WiFi stack at all:

```
choice LED_NODE_TRANSPORT
    prompt "Command transport"
    default LED_NODE_TRANSPORT_HTTP
    config LED_NODE_TRANSPORT_HTTP     bool "HTTP (development)"
    config LED_NODE_TRANSPORT_UART     bool "UART (booth)"
endchoice
```

`MINIMAL_BUILD ON` is already set in the root CMakeLists and should stay.

**`espressif/led_strip` must be pinned to 3.x on ESP-IDF 6.** Version 2.5.5
declares only `idf: '>=4.4'`, so the component manager installs it without
complaint — but its SPI backend uses `MALLOC_CAP_*` and `heap_caps_calloc`
without including `esp_heap_caps.h`, relying on a transitive include that IDF 6
removed. That file is compiled on any target with GPSPI whichever backend we
actually use, so selecting RMT does not avoid it.

## Testing

The scaffold already carries `esp_stubs` and linux-target support in
`idf_component.yml`. That is worth keeping: the parser and the animation math are
pure functions by design, so both can be host-compiled and unit tested with no
hardware. Rendering a mode at a given timestamp and asserting on the resulting
canvas is a normal unit test.

---

## Scaffold remediation

What is in `led-node/` today is the stock example. It needs:

| Item | Action |
|---|---|
| `project(simple)` | → `project(led_node)` |
| Kconfig menu "Example Configuration" | → LED node config (ring size, GPIO, offset, direction, transport choice) |
| `EXAMPLE_BASIC_AUTH`, `EXAMPLE_ENABLE_SSE_HANDLER` | Delete — unused |
| `/hello`, `/echo`, `/ctrl`, `/any` handlers | Delete, replace with `/cmd` |
| `pytest_http_server_simple.py` | Example's CI test for `/hello`+`/echo` — delete or retarget |
| `README.md` | Stock example README; describes endpoints that will not exist |
| `while (server) sleep(5);` in `app_main` | Replace with render task creation |
| `sdkconfig.defaults` | Empty; needs httpd stack size and RMT settings |

**One flag:** `example_connect()` pulls `protocol_examples_common` by
`${IDF_PATH}` path, so the build depends on Espressif's examples tree being
present, and WiFi credentials live in menuconfig as `EXAMPLE_WIFI_*`. Acceptable
for a dev-only build that gets compiled out — but it must not survive into the
booth build.

## Deliberately excluded

- **No mutexes.** One owner for mode state; everything else goes through the
  queue.
- **No dynamic allocation** in the render path. Fixed canvases (~1 KB for two
  60×4 linear buffers), fixed command struct.
- **No JSON, no binary protocol.** ASCII lines until they demonstrably hurt.
- **No OTA.** Dev flashing is over USB; the booth build has no network.
- **No REST surface.** One `/cmd` endpoint, one parser.

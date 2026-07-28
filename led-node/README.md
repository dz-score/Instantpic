# led-node

ESP32 firmware for the photobooth's LED ring: **60× SK6812 RGBW**, driven over
RMT, taking commands from the backend.

- **What the ring does:** [`Docs/LED_SPEC.md`](../Docs/LED_SPEC.md)
- **How the firmware is arranged:** [`Docs/LED_NODE_ARCHITECTURE.md`](../Docs/LED_NODE_ARCHITECTURE.md)

> **Status: scaffold. Never compiled.** There is no ESP-IDF toolchain on the
> machine this was written on, so the first `idf.py build` should be expected to
> surface errors. Nothing here has been run on hardware.

## Layout

```
main/                 wiring only: queue, render task, transport
components/
  protocol/           the one command parser  (host-testable)
  transport/          transport_http.c (dev) | transport_uart.c (booth)
  render/             canvas, output, mode manager, anim_*.c
```

The render task owns all mode state. Transports produce onto a command queue and
never touch it, which is why there are no mutexes. That queue is also the seam
the dev→booth transport swap happens at.

## Build

```
idf.py set-target esp32       # or esp32s3 / esp32c3 / esp32c6
idf.py menuconfig             # LED Node Configuration
idf.py -p PORT flash monitor
```

Under **LED Node Configuration**: ring size, data GPIO, `RING_OFFSET`,
direction, transport, and default brightness.

### Development (HTTP)

The default. The booth wiring occupies the USB port that flashing needs, so
during development commands arrive over WiFi instead and the cable stays free.

Set **WiFi SSID** and **WiFi password** under `LED Node Configuration` →
`Command transport` in `menuconfig`. They land in `sdkconfig`, which is
gitignored — do not put them in `sdkconfig.defaults`, which is tracked.

> Classic ESP32 is **2.4 GHz only**. A Windows PC hotspot often defaults to
> 5 GHz, and the resulting failure looks exactly like a wrong password.

`idf.py monitor` prints the address on association:
`wifi: connected — open http://192.168.x.x/`

```
curl "http://<ip>/cmd?c=CAPTURE"
curl -X POST --data "COUNTDOWN 3000" http://<ip>/cmd
```

`http://<ip>/` serves a page with a button per mode.

### Booth (UART)

Select `LED_NODE_TRANSPORT_UART`. WiFi is compiled out entirely.

> With `LED_NODE_UART_PORT=0` — the port wired to the USB bridge, which is how
> the Pi connects — the console shares that line. Set `CONFIG_ESP_CONSOLE_NONE`
> or `ESP_LOG` output will be interleaved into the protocol stream the Pi is
> parsing.

## Commands

ASCII, newline-terminated, identical on both transports.

| Command | Reply | Notes |
|---|---|---|
| `IDLE` | `OK IDLE` | |
| `PHASE <hue>` | `OK PHASE` | hue 0–359; the Pi owns the screen→colour table |
| `COUNTDOWN <ms>` | `OK COUNTDOWN` | node runs its own clock from here |
| `CAPTURE` | `OK CAPTURE` | **wait for this reply, then fire the shutter** |
| `RELEASE` | `OK RELEASE` | |
| `PRINTING` | `OK PRINTING` | times out to Error after 120 s |
| `FINISHED <ms>` | `OK FINISHED` | returns to Idle on its own |
| `ERROR <code>` | `OK ERROR` | code = number of heartbeat groups |
| `PING` | `PONG` | **every ~2 s** — this is what the link watchdog measures |

Unknown verbs return `ERR UNKNOWN` and are otherwise ignored, so the firmware
and backend can version independently.

`PING` is not optional. The watchdog measures time since any inbound line, not
since the last mode change — Idle runs for hours without a transition, and
without a heartbeat the node would drop into Link Lost at a perfectly healthy
booth.

## Hardware notes

- Strip gets **its own 5 V supply**. A Pi 5's official supply is 5 A total and
  cannot carry even the ~1.8 A capture load.
- **Inject power at both ends.** Capture holds near-max for seconds; single-end
  feed drops enough voltage that the far side goes dim and pink, mid-shot.
- **74AHCT125 level shifter.** 3.3 V logic into a 5 V strip is marginal.

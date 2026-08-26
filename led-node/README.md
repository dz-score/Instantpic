# led-node

ESP32 firmware for the photobooth's LED ring: **60× SK6812 RGBW**, driven over
RMT, taking commands from the backend.

- **What the ring does:** [`Docs/LED_SPEC.md`](../Docs/LED_SPEC.md)
- **How the firmware is arranged:** [`Docs/LED_NODE_ARCHITECTURE.md`](../Docs/LED_NODE_ARCHITECTURE.md)
- **How to test it without hardware:** [`Docs/LED_NODE_TESTING.md`](../Docs/LED_NODE_TESTING.md)

> **Status: builds on ESP-IDF 6, bench testing in progress.** Not yet run with a
> strip attached — nothing photographic (PWM banding, colour rendition, current
> draw, thermal shift) has been verified. See
> [`Docs/LED_NODE_TESTING.md`](../Docs/LED_NODE_TESTING.md) for what is and is
> not provable without hardware.

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

### HTTP — the transport that ships

The default, and the production transport. Commands arrive over WiFi and the
USB cable stays free for flashing.

This reverses [`Docs/LED_SPEC.md`](../Docs/LED_SPEC.md)'s original call for
serial. That reasoning was not refuted, only deferred — see
[`Docs/LED_UART_SWITCH.md`](../Docs/LED_UART_SWITCH.md) for what would trigger
the switch back and what it would involve.

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

### UART — not currently built

Select `LED_NODE_TRANSPORT_UART`. WiFi is compiled out entirely.

> **Never compiled or run.** `transport_uart.c` is fully written but sits behind
> `#if CONFIG_LED_NODE_TRANSPORT_UART`, and development has been HTTP-only. It
> also never calls `uart_set_pin()`, which is harmless on UART0 and silently
> deaf on UART1/2. Read
> [`Docs/LED_UART_SWITCH.md`](../Docs/LED_UART_SWITCH.md) before selecting it.

> With `LED_NODE_UART_PORT=0` — the port wired to the USB bridge, which is how
> the Pi connects — the console shares that line. Set `CONFIG_ESP_CONSOLE_NONE`
> or `ESP_LOG` output will be interleaved into the protocol stream the Pi is
> parsing.

## Commands

ASCII, newline-terminated, identical on both transports. The vocabulary:

```
IDLE   PHASE <hue>   READY   COUNTDOWN <ms>   CAPTURE   RELEASE
PRINTING   FINISHED <ms>   ERROR <code>   PING
```

**The specification is [`Docs/LED_PROTOCOL.md`](../Docs/LED_PROTOCOL.md)** —
argument ranges, reply strings, the seven error replies, line-format
tolerances and per-transport framing. Deliberately the only copy: this file
used to carry a second table, and two copies of a protocol drift exactly the
way the firmware's one-parser rule exists to prevent.

Three things that catch client authors out, all detailed there:

- `ERR` replies come back as **HTTP 200**. Parse the body, never the status.
- Only **one command may be in flight** at a time.
- `PING` every ~2 s is **not optional** — the watchdog measures time since any
  inbound line, not since the last mode change, so an idle booth would drop
  into Link Lost without it. It is also the recovery path back out.

## Hardware notes

- Strip gets **its own 5 V supply**. A Pi 5's official supply is 5 A total and
  cannot carry even the ~1.8 A capture load.
- **Inject power at both ends.** Capture holds near-max for seconds; single-end
  feed drops enough voltage that the far side goes dim and pink, mid-shot.
- **74AHCT125 level shifter.** 3.3 V logic into a 5 V strip is marginal.

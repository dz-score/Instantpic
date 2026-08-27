---
status: contingency plan; not executed
last-reviewed: 2026-07-31
applies-to-commit: 5a3221a
---

# LED_UART_SWITCH — Moving the LED Node to Serial

HTTP over WiFi is the **production** transport for the LED node. This document
exists because that reverses [LED_SPEC.md](LED_SPEC.md)'s original reasoning,
and the case for serial is still sound — it is being deferred, not refuted.

Everything needed to reverse the decision later is written down here so it does
not have to be rediscovered under pressure. Read this **before** building
anything on the Pi side, because a handful of cheap choices made now are what
keep the switch cheap.

---

## The decision, and the risk being accepted

**HTTP ships.** One transport to build, harden and test rather than two. The
ESP32's USB port stays free for flashing, and the Pi is spared a udev rule,
baud config, port permissions and device-node churn.

**The risk is real and deliberate.** [LED_SPEC.md](LED_SPEC.md) argued for
serial because a wedding venue is a hostile 2.4 GHz environment — a few hundred
phones plus whatever the DJ brought — and because retries land exactly in the
countdown-to-shutter window. None of that has stopped being true. The bet is
that a dedicated AP on a hand-picked channel, with the node as its only client,
keeps the tail latency small enough to not matter.

**The bet is falsifiable, and that is the point.** See the next section.

---

## What would trigger the switch

"If HTTP isn't reliable" has to mean something measurable, or it will be argued
about instead of decided. Instrument the Pi client from day one:

- Round-trip latency per command, **percentiles not averages** — p50, p95, p99.
- The `CAPTURE` → `OK CAPTURE` ack specifically, kept as its own series. It is
  the only command with a hard real-time requirement.
- Counts of transport timeouts, `ERR BUSY`, `ERR TIMEOUT`, and every entry into
  Link Lost.

Switch if the ack p99 approaches the shutter budget, or if Link Lost entries
appear at all during a session that was otherwise healthy.

> **A quiet-room test proves nothing.** 2.4 GHz congestion arrives with the
> guests. Bench numbers at 2 pm in an empty hall are not evidence about 9 pm
> with 150 phones in the room. Either capture the metrics at a real event, or
> treat the decision as still open.

---

## What the HTTP client must do now to keep the switch cheap

These are the only places where UART's constraints should influence HTTP-era
code. Everything else stays HTTP-shaped and uncoupled.

### 1. One command in flight — non-negotiable

`transport_common.c` has a **single reply queue of depth 1**. Command/reply
correlation is **positional**, not keyed.

HTTP hides this: overlapping requests get serialized by `esp_http_server`'s
single handler task and each still receives the right answer. UART cannot hide
it — replies come back as a bare line stream with nothing tying a reply to its
command.

Build the HTTP client with concurrent senders and it will work for months, then
the UART version is not a port but a redesign — undertaken on the worst possible
day, because by definition something already went wrong.

Enforce it **structurally**, not with a lock: a single owner task/thread owns
the transport, fed by a queue, callers await a future for the reply. That is
`camera_service.py`'s shape for blocking hardware IO, and it is the firmware's
own shape — one owner, no mutex, serialization that nobody can forget.

### 2. Keep the seam thin

`LedTransport` carries one line and returns one reply line. Above it,
`LedController` maps booth semantics to command lines and owns the heartbeat,
the ack policy and degradation. `state_machine.py` knows nothing about either.

The firmware's own seam is not a struct of function pointers — selection is at
compile time, so it is just a shared signature (`transport_start` /
`transport_stop`). The Pi picks its transport from config at runtime and does
need a real interface. **The contract carries over; the mechanism does not.**

- **The transport returns the raw reply line, unparsed.** `OK`/`ERR`
  interpretation lives above the seam so both transports produce identical
  semantics — the same argument as "one parser" in the firmware.
- **Normalize failures at the seam.** Link-down and link-timeout escape;
  `httpx` exceptions and status codes do not. `ERR *` replies are ordinary
  return values, not exceptions.
- **Timeouts are transport config.** Serial round-trip is ~5 ms, congested WiFi
  has a fat tail; one constant cannot serve both.

### 3. Nest config under a transport key

`led.http.host`, not `led_ip`. Adding a `led.uart.*` block later should not
restructure settings or touch the composition root.

### 4. Provide a drain/resync hook on the seam

The one place to add something HTTP does not need. Over HTTP every request is
self-framed and a timeout is contained. Over UART, a single desync — a stale
half-line in the buffer, a reply that arrives after its timeout — shifts the
positional correlation and **poisons every reply after it**.

Cost today: a no-op method. Cost retrofitted later: the nastiest class of bug in
the whole system, hunted at a venue.

### 5. Share the failure policy

Whatever the Pi does on a transport timeout or `ERR TIMEOUT` must live above
the seam and be identical for both transports. If the policy differs, the swap
is not behavior-preserving and the bench results do not transfer.

---

## What the switch actually involves

### Firmware

- Flip the Kconfig `choice` to `LED_NODE_TRANSPORT_UART`. WiFi compiles out
  entirely.
- `transport_uart.c` is **fully written** — line framing, overlong-line discard
  via `ERR TOOLONG`, shared `transport_submit`. It has **never been compiled or
  run**; it sits behind `#if CONFIG_LED_NODE_TRANSPORT_UART`.
- **`uart_set_pin()` is missing.** Harmless on UART0 where boot defaults route
  TX/RX, silently deaf on UART1/UART2 — which `CONFIG_LED_NODE_UART_PORT` lets
  you select. The config offers a combination that cannot work and reports no
  error.
- **The console shares UART0.** Set `CONFIG_ESP_CONSOLE_NONE` — there is a
  commented stub in `sdkconfig.defaults` — or `ESP_LOG` output interleaves into
  the stream the Pi is parsing.

> **Chip choice matters here.** On S3/C3/C6 the console moves to USB Serial
> JTAG and UART0 stays free, so you keep `idf.py monitor` while running the
> protocol. Classic ESP32 has no such peripheral: you lose the console exactly
> when you are debugging a new transport at a venue. If the hardware is not
> final, this is a reason to prefer an S3.

### Pi

- pyserial, port opened once and held. Blocking, so it belongs on the owner
  thread — which the design already has.
- **Drain stale bytes on open.** A half-line left from a previous run desyncs
  every reply that follows.
- **udev rule pinning the USB serial number to `/dev/led-node`.** A replug gives
  a new device node otherwise.
- Reopen-on-failure, since a replug is a normal event and not a fault.

### Operational

- **Bring serial up well before the event.** The firmware change is small; the
  surroundings are not.
- **Retune the `CAPTURE` ack timeout on serial.** The latency distributions are
  not comparable, and that timeout is what stands between a hiccup and a dark
  photo.
- **The ESP32's USB goes to the Pi**, so flashing from a dev machine is
  impossible while serial is wired. Plan the order of operations.
- Run the Link Lost recovery steps in
  [LED_NODE_TESTING.md](LED_NODE_TESTING.md) on the serial path. They have only
  ever been specified, never executed, on either transport.

---

## What does not change

The protocol. Both transports carry the identical ASCII line, parsed by the same
`command_parse()`, and the queue is the seam — nothing below it can tell which
transport is running. `PING`/`PONG` must exist on both, or the link watchdog's
first real test is in production.

As of `14a6f00` a `PING` alone recovers the node from Boot and Link Lost, so a
Pi client that resumes pinging after a reconnect is sufficient; it does not need
to send a mode command to restore the ring.

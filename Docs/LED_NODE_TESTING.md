---
status: procedure written; not yet run end to end
last-reviewed: 2026-07-31
applies-to-commit: 06c1c34
---

# LED_NODE_TESTING — Bench Procedure

How to verify `led-node` end to end **without the LED strip**, using the HTTP
transport and the browser preview.

The firmware runs perfectly well with nothing wired to the data pin — RMT does
not sense its load, so `led_strip_refresh()` transmits into open air and every
mode still runs. `GET /frame` serves the bytes the strip *would* have received,
so everything except the photons is observable.

Behavior being verified: [LED_SPEC.md](LED_SPEC.md).
Design under test: [LED_NODE_ARCHITECTURE.md](LED_NODE_ARCHITECTURE.md).

---

## Prerequisites

```
idf.py menuconfig      # LED Node Configuration
idf.py build flash monitor
```

- **Transport** = `HTTP (development)`
- **WiFi SSID / password** set to your hotspot
- The hotspot must be **2.4 GHz**. Classic ESP32 has no 5 GHz radio, and the
  failure looks exactly like a wrong password.

> **Windows:** in PowerShell `curl` is an alias for `Invoke-WebRequest`. Use
> **`curl.exe`** explicitly, or run these from Git Bash.

Substitute the node's address for `IP` throughout.

---

## Phase 1 — Boot

Expected on the monitor, in this order:

```
I (xxx) output: 60 px on GPIO 18, offset 0
I (xxx) modes: render task started at 125 Hz
I (xxx) wifi: connecting to "yourssid" (2.4 GHz only)
I (xxx) transport_http: listening on port 80
I (xxx) led_node: ready
I (xxx) wifi: connected — open http://192.168.x.x/
```

**`connected` arriving after `ready` is correct**, not a race. WiFi startup is
deliberately non-blocking: the HTTP server binds to any address, and the render
task is already animating throughout. The node is required to look correct with
no host talking to it.

## Phase 2 — The parser

The highest-value target: both transports share this code, so anything proven
here holds after the switch to UART.

```bash
curl.exe "http://IP/cmd?c=PING"            # PONG
curl.exe "http://IP/cmd?c=ping"            # PONG         case-insensitive
curl.exe "http://IP/cmd?c=PHASE%20280"     # OK PHASE
curl.exe "http://IP/cmd?c=PHASE+280"       # OK PHASE     '+' decodes to space
curl.exe "http://IP/cmd?c=PHASE%200"       # OK PHASE     hue 0 is valid
curl.exe "http://IP/cmd?c=PHASE%20360"     # ERR RANGE    359 is the maximum
curl.exe "http://IP/cmd?c=COUNTDOWN%200"   # ERR RANGE    must be > 0
curl.exe "http://IP/cmd?c=WOBBLE"          # ERR UNKNOWN
curl.exe "http://IP/cmd?c=IDLE%205"        # ERR UNKNOWN  strict: no extra tokens
curl.exe -X POST --data "COUNTDOWN 3000" http://IP/cmd
```

Ranges: `PHASE` 0–359, `COUNTDOWN` and `FINISHED` 1–60000 ms.

Unknown verbs must return `ERR UNKNOWN` and change nothing. That is what lets
the firmware and backend version independently.

> Known cosmetic inconsistency: an overlong line returns `ERR TOOLONG` over POST
> but `ERR NOCMD` over GET, because the query parser truncates before we see it.

## Phase 3 — Frame rate

```bash
curl.exe http://IP/state     # note "frames"
# wait 10 seconds
curl.exe http://IP/state
```

The delta should be **~1250** (125 fps, an 8 ms frame). Materially lower means
the render task is being starved and every animation timing below is suspect.

## Phase 4 — Modes

Open `http://IP/` and leave the heartbeat box checked.

| Command | What to verify |
|---|---|
| `IDLE` | Slow warm breathing on a ~6 s cycle, rotation so slow it is barely perceptible. **It should be boring** — that is the four-hour requirement, not a defect. |
| `PHASE 280` | A bright head sweeps once around in ~400 ms *laying the colour down behind it*, then holds with a faint shimmer. Try 0 / 120 / 240 to check hue mapping. |
| `COUNTDOWN 3000` | **Count the laps: exactly 3, one per second.** On the final lap the head brightens and the trail lengthens. |
| `COUNTDOWN 5000` | **5 laps, and the final-second lift still lands on the last one.** This proves the firmware is count-agnostic — a different countdown length is a parameter, not a code change. |
| `CAPTURE` | Ramps to full white over 100 ms then freezes. Completely static. |
| `RELEASE` | Smooth 200 ms cross-fade back to Idle. |
| `PRINTING` | Three colour bands plus a genuine dark gap, one revolution per 2 s, constant speed and direction. |
| `FINISHED 4000` | Sparkles over a warm base, thinning as it goes, then **auto-returns to IDLE** after 4 s. |
| `ERROR 1` | One double-pulse, then a long pause. |
| `ERROR 3` | Three double-pulse groups, then the pause. Count them — that is the on-site diagnostic. |

**Press the board's reset button** to watch Boot again: red, green, blue, white
laps at ~500 ms each, then the slow orbiting dot at 4 s per lap.

## Phase 5 — The three claims worth judging

These are the design assertions that were never verifiable in code review.

**Sub-pixel rendering.** Watch a single countdown lap closely. The head should
*glide*. Visible stepping means the anti-aliased primitives are not doing their
job — at 60 px and one revolution per second, the head crosses a pixel every
16.7 ms.

**Linear-space compositing.** Send `IDLE`, then `PHASE 280`, and watch the
200 ms transition. It must fade smoothly. **A visible dip toward dark through
the midpoint means gamma is being applied before the blend instead of after.**

**Capture bypasses global brightness.** This one is exactly measurable:

```bash
curl.exe "http://IP/cmd?c=CAPTURE"
curl.exe http://IP/frame
```

Every value in `px` must be **255**.

If the bypass were broken and the default 70% brightness leaked in, the values
would read **217** — not 178. The scaling happens in linear light and is *then*
gamma-encoded: `255 × 0.70^(1/2.2) ≈ 217`. Either way the key light would be
running below full and quietly underexposing every photo.

## Phase 6 — Safety behaviours

The ones that matter when this is unattended at a wedding.

**Link watchdog — entry.** Uncheck the heartbeat box and wait 10 s. The ring goes
amber and the monitor logs `link lost`. `/state`'s `since_rx_ms` should climb
visibly beforehand.

**Link watchdog — recovery.** From Link Lost, re-check the heartbeat box and
**send nothing else**. The box sends `PING` and only `PING`, so this is the real
test: within ~2 s the monitor logs `link back` and `/state` reports `IDLE`.

> Do not substitute a button press for the checkbox. Every other control on the
> page sends a mode command, which enters a mode on its own and would pass this
> step whether or not recovery works. Heartbeat alone is the assertion.
>
> This is the case that shipped broken until 14a6f00: `PONG` came back while the
> mode stayed `LINKLOST`, so the Pi saw a healthy link and the ring showed an
> error at a working booth. The step below existed and would have caught it —
> it had simply never been run.

**Boot recovery.** Reset the node with the heartbeat box already ticked and
touch nothing else. It must leave the boot pattern and reach Idle on pings
alone, without a mode command.

**Capture timeout.** Send `CAPTURE` and wait 30 s without `RELEASE`. Monitor
logs `capture never released` and the mode drops to Idle. This is what stops a
dead host stranding the strip at full white — the highest-current, highest-heat
state in the system.

**Printing timeout.** Send `PRINTING` and wait 120 s. Logs `printing timed out`
and the mode goes to Error, rather than rolling ink forever behind a jammed
printer.

**WiFi reconnect.** Turn the hotspot off, watch the retry messages, turn it back
on. It must rejoin without a reset.

---

## Phase 7 — With the Pi driving it

Phases 1–6 exercise the node from a browser. This one exercises the booth: the
FSM sending real commands at real moments, which is the only place the CAPTURE
ack sits inside the shot and the only place the numbers mean anything.

Everything here runs on the Pi with the strip and the M50 attached. Setup is in
`Docs/DEPLOYMENT_GUIDE.md` §11.

**1. Address.** The node associated and holds its reserved address:

```bash
sudo arp -a | grep -i wlan0
```

**2. Link.** Admin → System → LED Ring: enabled, address entered, **Test Ring**
answers `PONG`. On a quiet AP this should be single-digit milliseconds; anything
above ~30 ms is worth understanding before continuing.

**3. Single shot.** Run a full session. The ring must be **white and steady
before the shutter**, not still ramping — the ack is what buys that, so this is
the assertion the whole handshake exists for. Check the photo, not just the
ring: a frame caught mid-ramp looks underexposed and slightly colour-shifted,
not obviously "wrong".

**4. Three-shot collage.** Watch the gap between shots. The node starts its next
countdown when the shot completes, while the browser waits `shot_interval_ms`
first, so the ring finishes its sweep slightly early and holds at the top. That
is expected and benign — `anim_countdown` clamps elapsed to duration. A ring
that goes dark, wraps, or restarts is not.

**5. Pull the node's power mid-session.** The booth must keep taking photos.
`led_link_error` appears in the log, the LED Ring card goes red within a few
seconds, and nothing about the guest's session changes except the light. Restore
power: the heartbeat alone must bring the ring back to Idle — no restart, no
reflash. (Phase 6 asserts this at the node; this asserts the Pi's half of it.)

**6. Record the numbers.**

```bash
curl -s localhost:8000/api/diagnostics | python3 -m json.tool
```

`led.latency_ms.CAPTURE` p50/p95/p99, and `led.counts` for `link_error:*` and
`rejected:*`. Those are the Pi's view; the node's own Link Lost entries are
node-side, so read them from the monitor or from `http://<node>/state` — a
`link lost` line in an otherwise-healthy session is the signal, and the counters
alone will not show it. This is the baseline
`Docs/LED_UART_SWITCH.md` compares against, and the whole reason the
instrumentation exists.

> **A quiet room proves nothing.** The condition that decides HTTP's fate is a
> venue full of phones on 2.4 GHz, and it cannot be reproduced on a bench.
> Re-run this step at the event and compare against the baseline — a p99
> approaching the shutter budget, or a Link Lost in an otherwise-healthy
> session, is the trigger to build the UART version.

---

## Troubleshooting

**Boot loop with `E BOD: Brownout detector was triggered`,** dying right after
`phy_init`. Not a firmware fault — RF calibration is the largest current surge
in the whole boot, and the 3.3 V rail is sagging. In order: swap to a short
thick data-rated USB cable (fixes it most of the time), use a motherboard USB
port rather than a hub, add a 470–1000 µF electrolytic across the board's supply
and ground, or feed 5 V into VIN externally. Lowering
`CONFIG_ESP_BROWNOUT_DET_LVL_SEL` silences the symptom but trades a clean reset
for unpredictable execution on a sagging rail — use it to confirm the diagnosis,
not to fix it.

**Never associates.** Check the hotspot is 2.4 GHz before anything else.

**`Detected size(4096k) larger than the size in the binary image header`.**
Harmless, but the build is configured for less flash than the chip has. Set
`CONFIG_ESPTOOLPY_FLASHSIZE_4MB`.

---

## What this procedure cannot tell you

Everything above validates timing, state transitions, geometry and the render
pipeline. It cannot validate anything photographic:

- **PWM banding** at 1/200 s and faster
- **Colour rendition** — whether W-only white flatters skin, and whether the
  `rgb_trim` values need setting
- **Current draw** against the ~1.2 A prediction for Capture
- **Thermal colour shift** across a sustained Capture

Those need the strip and the M50, and they are what settle the two open
questions: whether Capture holds continuously across all three shots or cycles
per shot, and what `TRIM_R/G/B` should be.

The browser preview also sums the W channel into R/G/B to approximate the white
die, so on-screen white looks cleaner than a real RGBW package does at close
range. Trust it for motion and timing, not for colour.

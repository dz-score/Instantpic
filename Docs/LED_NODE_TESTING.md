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

**Link watchdog.** Uncheck the heartbeat box and wait 10 s. The ring goes amber
and the monitor logs `link lost`. Re-check it and it recovers. `/state`'s
`since_rx_ms` should climb visibly beforehand.

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

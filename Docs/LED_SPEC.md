# LED_SPEC — Photobooth LED Ring Behavior

Design specification for the booth's LED ring: **60× SK6812 RGBW**, driven by a
dedicated **ESP32** (`led-node/`) that receives mode commands from the backend
over **USB serial**.

**Status:** design spec, written 2026-07-25. Not yet reconciled against the
existing `led-node/` firmware — where the two disagree, that is an open item,
not a decision.

---

## Why a separate microcontroller

Not to offload CPU. Driving SK6812s from the Pi via DMA costs almost nothing
next to capture, compositing, and spooling. The real reasons:

1. **Timing isolation.** Linux is not real-time. The protocol is ~1.25 µs/bit
   with tight tolerances, and the failure mode when the host stalls is not
   "slow" — it's a glitched frame that *latches* until the next refresh. The
   ESP32's RMT peripheral is hardware-timed and cannot jitter.
2. **Audio conflict.** The classic PWM method on the Pi shares hardware with
   onboard analog audio. The booth has a speaker and amp
   (see [CAMERA_NOTES.md](CAMERA_NOTES.md) on the amp whine).
3. **Electrical separation.** The strip's 5 V rail has real inrush and ground
   bounce. Sharing it with the Pi buys brownouts, SD corruption, and USB camera
   dropouts that present as software bugs.
4. **Independent failure.** LEDs keep running if the backend crashes; firmware
   can be reflashed without bouncing the booth service.

**Transport is USB serial, not WiFi.** A wedding venue is a hostile 2.4 GHz
environment — a few hundred phones plus whatever the DJ brought. Retries land
exactly during the countdown-to-shutter window. Serial is wired, powers the
ESP32, survives replug, and gives a console for on-site debugging.

**The ESP32 has no inputs.** It is a pure sink. Everything it displays is a
consequence of a command from the Pi.

---

## Hardware budget

| Case | Per LED | Total @ 5 V |
|---|---|---|
| Capture, W channel only | ~20 mA | 1.2 A |
| Capture, W + RGB trim | ~30 mA | 1.8 A |
| Absolute max, all four channels | ~80 mA | 4.8 A |
| Typical idle animation | ~10 mA | 0.6 A |

A 5 V / 5 A supply covers everything with margin.

**The strip gets its own PSU.** A Pi 5's official supply is 5 A *total*, so even
the realistic 1.8 A capture load cannot hang off it.

**Inject power at both ends.** The first pixel otherwise carries the entire
strip current, and Capture holds near-max for *seconds*. Single-end feed drops
enough voltage that the far side of the ring goes dim and pink — mid-shot, in
the key light. Two wires instead of one.

**Level shifting.** 3.3 V logic into a 5 V strip is marginal. Use a 74AHCT125.

---

## Global conventions

**Geometry.** Define `RING_OFFSET` (pixel index at visual 12 o'clock) and
`RING_DIRECTION` (clockwise as seen by the guest). Every animation is expressed
in *degrees*, never pixel indices, and the renderer maps through these two
constants. The physical seam can then land wherever mounting is convenient and
gets corrected in software.

**Framerate.** Render at 120 fps. Hardware ceiling is ~400 Hz
(60 px × 32 bits × 1.25 µs ≈ 2.4 ms/frame), so this is comfortable.

**Sub-pixel rendering.** Any moving element straddles pixels with fractional
brightness. No animation snaps to whole-pixel steps.

**Gamma.** All brightness values pass through a gamma LUT (γ ≈ 2.2) before the
driver. Without it, fades appear to jump near black and flatten near full —
every breathing and trailing effect below depends on this.

**Mode transitions.** Cross-fade 200 ms between modes. Exceptions: entering and
leaving Capture, which have their own ramps.

**Global brightness.** One runtime scalar, settable from the Pi, applied to
every mode **except Capture**. Venue lighting varies; this wants trimming during
setup without a reflash.

**Timing ownership.** The Pi sends a mode and its parameters *once*. The ESP32
runs its own clock from receipt. The Pi does not stream frames and does not tick.

---

## 1. Idle — "The Ambient Beacon"

**Intent.** Draw people over from across the room. Survive four hours of being
looked at without becoming irritating.

**Visual.** W-dominant warm base at ~30%, breathing slowly between ~25% and ~40%
on a ~6 s cycle. Under it, a low-saturation color wash drifts through a narrow
hue range, and the whole field rotates once every ~25 s. No hard edges, no
discrete elements.

**Parameters.** `brightness`, `base_hue`, `hue_range`.

**Notes.** Runs most of the night, so it sets the thermal baseline (~0.4 A). The
tuning rule that matters: **nothing here has a period under ~5 s.** Fast motion
reads as urgent, and urgent for four hours is exhausting to be near. Lean on W
with color as a tint — saturated RGB at idle is what makes installations look
cheap.

## 2. Playful — "The Interactive Guide"

**Intent.** Acknowledge that the guest advanced a step in the UI flow.

**Visual.** Two parts. On a new phase, a bright head sweeps once fully around the
ring in ~400 ms, leaving the new hue behind it. Then it settles: that hue at
moderate saturation with a slow shimmer, quiet enough to sit behind
screen-reading.

**Parameters.** `hue`, `saturation`.

**Notes.** The firmware never learns the UI's screen list. The Pi sends a
*color*; the ESP32 owns the *transition*. Which screen maps to which hue is a
table in the backend — screens can be added, removed, or retuned without a
toolchain. Sweep direction is always the same; consistent direction reads as
forward progress. Keep the hold noticeably more saturated than Idle so the two
are never confused at a glance.

## 3. Countdown — "The Ticking Clock"

**Intent.** Build anticipation, and cue the guest to be ready at the right
instant.

**Visual.** Background drops to a dim W wash. A bright head sweeps at exactly
**one revolution per second**, dragging a ~40° trail that falls off smoothly
behind it.

**Final second:** head brightness lifts to full and the trail extends to ~90°.
Same color, same speed — only intensity and length change.

**Parameters.** `duration_ms`.

**Notes.** 60 px at one rev/s is one pixel per 16.7 ms — sub-pixel rendering is
mandatory here or the sweep looks steppy.

The ring deliberately does **not** encode which second it is; the screen displays
the number, and duplicating it in the periphery buys nothing. This also keeps the
firmware count-agnostic: it gets a duration and spins until told otherwise, so a
5-second countdown is a parameter rather than a code change.

The final-second lift comes from comparing elapsed time to duration — still no
knowledge of the count. Its purpose is not information but timing: the guest is
looking at the lens, and peripheral vision is poor at reading numbers and
excellent at noticing "that got brighter." It's a nudge to smile *now* rather
than half a second late.

Trail color should be W-tinted, not saturated — it's the half-second before a
white key light comes on, and a colored trail fights that.

## 4. Capture — "The Studio Over-Drive"

**Intent.** This is the key light. Everything else in this document is
decoration; this mode is photographic equipment.

**Visual.** Ramp W to 255 over 100 ms. Hold, absolutely static, for the full
capture phase. Ramp down over 200 ms.

**Parameters.** `rgb_trim` (small fixed offset for color temperature, set once at
installation). Held until released.

**Notes — all load-bearing:**

- **Full brightness only.** SK6812 dimming is PWM in the high hundreds of Hz. At
  1/200 s or faster you sample a fraction of a cycle and get banding plus
  shot-to-shot exposure drift. At 255 the die is simply on. If you need less
  light, move the strip or stop down — **brightness is not a knob inside this
  window.**
- **W channel, not mixed RGB white.** Mixed white is a three-spike spectrum: it
  renders skin blotchy and makes fabric color unrecoverable in post. Use
  `rgb_trim` only to nudge temperature, never to synthesize the white.
- **Zero animation.** Any motion means uneven lighting across a burst.
- **Power.** 1.2–1.8 A held for seconds. This is where both-end injection earns
  its keep.
- **Ramp, don't snap.** 100 ms is imperceptible on camera and much kinder to the
  PSU and inrush.

**Open item:** does this hold across all three shots continuously, or cycle per
shot? Continuous full W heats the strip, and hot SK6812s shift color across the
sequence.

## 5. Printing — "The CMYK Ink Roller"

**Intent.** Communicate "working, please wait" for an unknown duration.

**Visual.** Four bands — cyan, magenta, yellow, and a genuine gap of off pixels —
rotating steadily in one direction, one revolution per ~2 s. Soft edges between
bands, hard edge into the gap.

**Parameters.** `speed`.

**Notes.** Duration is unknowable, so this is hold-until-released with a
**timeout of ~120 s**, after which it falls to Error. Without that, a jammed
printer leaves the booth cheerfully rolling ink forever.

The off-gap is what makes rotation legible — a continuous color wheel at this
speed reads as a blur. Steady speed, constant direction; resist making it
accelerate.

## 6. Finished — "The Hollywood Sparkle"

**Intent.** Reward. The emotional punctuation on the whole interaction.

**Visual.** Warm W base at ~40%. Over it, random pixels flare to full W with a
fast attack (~30 ms) and slower decay (~400 ms). Sparkle density starts high and
decays over the mode's life so it resolves rather than just stopping.

**Parameters.** `duration_ms`, `density`.

**Notes.** Sparkles on the W channel are crisp and read as *light*; on mixed RGB
they read as *pixels*. Auto-returns to Idle when the duration expires, so a guest
walking away doesn't leave the ring celebrating at nobody. Peak ~0.8 A.

## 7. Error — "The Maintenance Heartbeat"

**Intent.** Tell the operator something is wrong without alarming a room full of
guests.

**Visual.** Deep red, low brightness (~15%). A double-pulse — two beats, then a
~1.5 s pause, like a heartbeat. Deliberately calm.

**Parameters.** `code` (optional).

**Notes.** If `code` is used, express it as the number of double-pulse groups
before a longer pause. That gives on-site diagnosis without a laptop, which is
worth a lot at 6 pm at a venue. **Must be visually distinct from Link Lost** —
these are opposite diagnoses.

---

## 8. Boot / Pre-Link

**Intent.** Cover the ~30 s where the ESP32 is alive and the Pi is not. Also
serves as the wiring self-test.

**Visual.** On power-up, sweep each channel around the ring in turn — R, G, B,
then W, one revolution each at ~500 ms. Then settle into a single dim W dot
orbiting slowly (~4 s per revolution) until the first valid command arrives.

**Notes.** The channel sweep is a free full self-test: four clean laps verify all
60 pixels, all four dies, the data line, and the level shifter — in two seconds,
with no tooling. That's the check you want during venue setup. The orbiting dot
afterward reads unambiguously as *waiting*, not *broken*.

## 9. Link Lost

**Intent.** The ESP32 noticing the Pi went silent — as distinct from the Pi
reporting a fault.

**Visual.** Amber, whole ring, slow symmetric pulse (~2 s period) between 10% and
30%. No motion, no direction. Clearly different in both color and character from
the red heartbeat.

**Entry.** No valid command for 10 s.

**Notes.** One safety rule: **if the link drops while in Capture, leave Capture.**
Ramp down and enter this mode. A dead host must never strand the strip at full
white — the highest-current, highest-heat state in the system, held indefinitely
with nobody watching.

On reconnect, obey whatever the Pi sends next; do not try to restore the previous
mode.

---

## Protocol principles

Derived from the above; the concrete wire format belongs in
[API_PROTOCOL.md](API_PROTOCOL.md) once settled.

1. **Policy on the host, mechanism on the device.** The Pi decides *what color*
   and *how long*; the ESP32 decides *how it moves*. Retuning the palette must
   never require a reflash.
2. **Send state, not frames.** One command per transition, with parameters. No
   per-tick messages — serial arrival jitter is visible in continuously-drawn
   elements like the countdown arc.
3. **Anything that gates a photo is acknowledged; everything else is
   fire-and-forget.** Capture is the only command whose lateness can ruin an
   unrepeatable frame, so the Pi sends it, waits for the ack, *then* triggers the
   shutter. Decorative modes need no reply — a 200 ms delay in Idle or Printing
   is imperceptible. This makes correctness depend on a handshake rather than on
   the transport being fast, which is also what would make a future move to a
   network transport safe.
4. **Unknown commands are ignored, not faulted.** The two artifacts version
   independently; drift must degrade gracefully.
5. **Every hold-forever mode has a timeout.** Printing and Capture both wait on
   external events that can fail silently.
6. **The ESP32 is self-sufficient at boot.** It looks correct with no host
   talking to it at all.

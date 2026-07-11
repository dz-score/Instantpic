# CAMERA_NOTES — Canon EOS M50 Field Knowledge

Hard-won facts about how **this specific camera body** behaves over USB/PTP
(gphoto2/libgphoto2), gathered during the July 2026 investigations (the
"35s whine" saga, the "first preview always fails" fix, and the "~3s
pre-shutter stall" characterization). Everything here is **log-proven on the
booth hardware** unless marked otherwise. Read this
before touching `backend/camera_service.py`. For step-by-step diagrams of
the startup and countdown→reveal flows, see
[CAMERA_SEQUENCES.md](CAMERA_SEQUENCES.md).

---

## Table of Contents

- ⭐ [Hard-Won M50 Findings (the short list)](#hard-won-m50-findings-the-short-list)

1. [The Golden Rules (do / don't)](#1-the-golden-rules-do--dont)
2. [The Wedged First Session](#2-the-wedged-first-session)
3. [Live-View Behavior Under Sustained Polling](#3-live-view-behavior-under-sustained-polling)
4. [Camera Widgets: What They Mean, What Not to Touch](#4-camera-widgets-what-they-mean-what-not-to-touch)
5. [The 35-Second "Camera Whine" That Wasn't](#5-the-35-second-camera-whine-that-wasnt)
6. [Preview ↔ Capture: Managing the Transition](#6-preview--capture-managing-the-transition)
7. [Capture Pipeline Facts](#7-capture-pipeline-facts)
8. [Diagnostic Playbook](#8-diagnostic-playbook)
9. [Timeline of Investigations](#9-timeline-of-investigations)

---

## Hard-Won M50 Findings (the short list)

The distilled, non-obvious behaviors of **this M50 body over gphoto2** that we
could **not** find documented anywhere public and spent hours proving on the
hardware (probe: `backend/tools/preview_stall_probe.py`). Each links to the
section with the evidence. If you only read one part of this file, read this.

**Periodic live-view stall (§3)**
- Sustained live view stalls **~3.0s and returns `[-1]`** on a **free-running
  ~9s cycle** (~6s healthy → ~3s stall), then repeats. The onset is a cliff,
  not a ramp.
- The trigger is **elapsed live-view time (~6s), not frame count or fps** —
  5 fps stalls exactly as often as 20 fps. Polling slower does not help.
- The per-frame `wait_for_event` **flush does NOT prevent it** (commonly
  assumed to; false on this body).
- **Standby/resume does NOT reset the clock** — it free-runs on camera
  live-view time, so you cannot dodge the stall with countdown/standby timing.
- A real **`capture_image` DOES reset it**: it opens a guaranteed ~6s
  stall-free window (then the cycle resumes). The reset is imperfect — ~10% of
  even tightly-chained captures still land in a stall.

**Wedged first session, caused by a capture (§2)**
- Taking a photo leaves the camera in a state that **wedges live view on the
  next process/boot**: config reads work, but every `capture_preview` stalls
  ~3s `[-1]`.
- It **survives a clean `camera.exit()` and process death** — it is caused by
  the capture, **not** by an unclean kill. A run that took no photos boots clean.
- It does **NOT self-clear**: neither idle time (a 5-min gap still boots
  wedged) nor continuous polling alone (27s / 9 stalls, never healed) clears it.
- Exactly **two** ways out: (1) a **`capture_image` clears it instantly**;
  (2) **~2 stalls (~6s) of polling THEN an `exit()+init()`**. Both parts of (2)
  are required — a re-init *before* ~2 stalls of priming does nothing, and the
  priming does not carry across a re-init (each fresh session needs its own).

**Widgets & session hygiene (§4, §1)**
- The `output` widget reads **`MOBILE2`** and the camera manages it itself;
  **never write it** (writing `TFT` killed previews for the whole session). A
  written value does **not persist** — it's back to `MOBILE2` on the next init.
- **`viewfinder=0` breaks the next preview session** (persistent `[-1]` until
  re-init). Don't set it.
- An **in-place session rebuild** inside `init()` right after a failed warmup
  **poisons every subsequent session** — only the exit-old-handle-then-init
  heal is safe.

**Non-camera gotcha (§5)**
- The "camera whine near the shutter" was the **speaker amplifier** (a live Web
  Audio context holding the output device open), not the camera. Mistrust
  ear-based localization of noises near the rig.

---

## 1. The Golden Rules (do / don't)

| Rule | Why |
|---|---|
| **Never write the `output` widget** (house rule) | Writing `'TFT'` kills every preview for the session — proven here. Upstream does sanction writing `PC`/`MOBILE`/`MOBILE2` to select a preview size, but that's untested on this body and we don't need it. `MOBILE2` as its value is **benign** — see §4. |
| **Never set `viewfinder=0`** | Breaks the *next* preview session with persistent `[-1]` until re-init. |
| **Don't re-init to "heal faster" *before* the session has polled ~2 stalls** | The wedged first session (§2) clears only after ~2 stalls of polling followed by an `exit()+init()` — a re-init *before* that priming doesn't heal, and polling alone never does. Re-initting after every single stall (tried and reverted in this investigation) never accumulates the priming → never heals, just adds latency. An in-place session rebuild is worse — it poisons every subsequent session (`7237e7a`/`832ab8e`, reverted `a4f4d51`). Correct = the worker-cascade heal: warmup stall (#1) → 1 worker stall (#2) → `connected=False` → re-enter `init()` (old handle exited first). |
| **Flush events (`wait_for_event`) around every capture** | The post-trigger flush drains `GP_EVENT_FILE_ADDED`; skip it and events pile up and poison the next preview session. (Note: the per-frame flush does **not** prevent the periodic ~6s live-view stall — measured, §3.) |
| **Chain multi-shot captures tight (`shot_interval_ms + countdown` < ~5s)** | A *capture* opens a ~6s stall-free window (§3); the next shot must fire inside it. `shot_interval_ms` is 1s for this. Standby does NOT open a window — only a capture (or a completed stall / re-init) resets the clock. |
| **Don't rely on countdown/standby timing to dodge the stall** | The ~6s stall clock free-runs on camera time; standby/resume/rate/flush do NOT reset it (§3, disproven the earlier ≤5s idea). |
| **Stop the booth with `stop.sh`, not a hard kill** | SIGTERM lets `camera.exit()` close the PTP session cleanly (verified: `camera_exit` logs on every stop). Good hygiene — but it does **not** prevent the wedged first session after a run that took photos; that wedge survives a clean exit; the worker heals it in ~9s (§2). |
| **Prefer letting the camera rest (standby/watchdog) over always-on polling** | Sustained polling exposes the periodic stall (§3); resting resets the clock for the next shot. |
| **Mistrust ear-based localization of noises near the camera** | The famous "camera whine" came from the speaker amplifier (§5). |

Priorities when trading off (per operator): **shutter latency and capture
reliability outweigh idle-time niceties.**

---

## 2. The Wedged First Session

**Symptom:** on app launch, the first PTP session has broken live view —
`camera.init()` succeeds, config reads work fine (widget dump prints), but
**every** `capture_preview()` blocks ~3.0s and returns `[-1] Unspecified
error`. The worker heals it in ~9s (see below), before any guest taps, and
the booth runs normally.

**What actually triggers it (corrected 2026-07-11 — supersedes the earlier
"stale session from a hard kill" root cause):** the differentiator is
**whether the previous run fired a real `capture_image`**, NOT whether the
previous process was shut down cleanly:

- A run that **took no photos** → next launch warms up clean on the first init.
- A run that **took a photo** → next launch is wedged, *even after a clean
  `stop.sh`*. Confirmed on hardware: `stop.sh` traps SIGTERM and
  `camera.exit()` runs cleanly (`camera_exit` "Camera connection closed
  cleanly" logs on **every** stop), yet the post-capture launch still wedges.

So a real capture leaves M50 state that **survives a clean PTP `exit()` and
process death**. (This is why the old "hard kill left no `exit()`" story
looked right — early on the booth was always hard-killed *and* always took
photos before the kill; the photo was the real cause, not the kill.)

**How the wedge clears (proven 2026-07-11 with `preview_stall_probe.py
--heal-probe`, multiple runs).** It does **not** self-clear — neither idle time
(a 5-min gap between `stop.sh` and relaunch still boots wedged) nor polling
alone (`--heal-strategy poll`: 27s / 9 back-to-back ~3.0s stalls, never healed)
clears it. There are exactly **two** ways out:

1. **A `capture_image` — clears it instantly.** (`--heal-strategy capture`: one
   real shutter, then preview is healthy in ~5ms.) We do **not** use this at
   startup — it fires an audible shutter with no guest around.
2. **~2 stalls (~6s) of live-view polling, THEN an `exit()+init()`.**
   (`--heal-strategy reinit`: 2 stalls → re-init → healed ~8s, reliable across
   runs.) **Both parts are required** — polling alone never heals (route 1's
   `poll` result), and a re-init *before* ~2 stalls of priming doesn't heal
   either (the inline heal below re-init'd after a single stall and never
   recovered). The priming does **not** carry across a re-init: each fresh
   session needs its own ~2 stalls before the exit+init.

**How it's handled (worker heal, originally commit `5c5b9c0`, later tightened).**
The heal takes route 2: poll ~2 stalls, then `exit()+init()`.

- `init()`'s warmup preview failing is the reliable wedge signature → sets
  `_warmup_failed` and logs `camera_warmup_fail` (WARN). That failed warmup
  grab is **stall #1** of the priming.
- While `_warmup_failed`: the worker trips the disconnect cascade after **1**
  more stall (`error_limit = 1`; that's stall #2 → the 2 stalls the re-init
  needs), and the idle watchdog **defers standby** so the heal isn't stranded
  waiting for a viewer.
- The worker cycles `connected=False` → re-enters `init()` (old handle exited
  → fresh session) → warmup now succeeds. Self-correcting: if it doesn't, the
  worker just runs another 2-stall cycle.
- Result: the camera is ready **~9s after boot** (was ~12s at `error_limit=2`
  / 3 stalls), on the attract screen, before a guest ever taps — which is the
  whole point: it makes the guest's **first** capture work. (Before the heal
  existed, the first shot failed every launch and only the 2nd worked, because
  a `capture_image` is route 1 and the guest's first shot paid for it.)
- `preview_generator()` refreshes `_last_preview_request` on **every poll
  slice**, so a viewer staring at a black stream counts as present —
  previously the watchdog judged them absent after 10s and paused the worker
  mid-heal.
- The error counter resets when the cascade trips, so a healed session
  doesn't inherit a hair trigger where one transient stall re-disconnects.

**Tried and reverted (2026-07-11):**

- An inline warmup-heal loop in `init()` (re-init up to 3× immediately on
  warmup fail) — it re-init'd after **every single stall**, so no session ever
  got the ~2 stalls of priming the exit+init needs; it never healed inline and
  just burned ~1.5s of open overhead per cycle (~22s boot). The fix is the
  opposite: let the session poll ~2 stalls *before* the re-init (which is what
  `error_limit=1` does — see above).
- Setting `output='Off'` before `exit()` on shutdown — a no-op; `output` reads
  back `MOBILE2` on every boot init (§4), so the write doesn't persist.

**`stop.sh` is still correct hygiene** (clean PTP close, no dangling USB
session) — it just does not prevent the post-capture wedge.

---

## 3. Live-View Behavior Under Sustained Polling

Characterized precisely with `backend/tools/preview_stall_probe.py`
(2026-07-10 — a controlled fps sweep + standby/resume cycle test on the booth
body, isolating `capture_preview()` from all the standby/capture/re-init
noise of a real session).

- **The stall is TIME-triggered, not frame- or rate-triggered.** The first
  `[-1]` stall lands **~6.0s after live view starts, at every rate tested** —
  fps 5/10/15/20 all first-stalled at 6.01–6.11s, while the frame count at
  the stall scaled with rate (28 → 60 → 72 → 91). So polling *slower does not
  help and faster does not hurt*: you get ~6s of healthy live view, then a
  stall, regardless of rate.
- **Stall duration is a rock-solid ~3.0s** (3.00–3.07s across dozens of
  stalls) that clears on its own. Rhythm: ~6s healthy → ~3s stall → ~6s
  healthy… (~9s stall-to-stall; a second stall sometimes lands back-to-back).
  **The onset is a cliff, not a ramp** — frames are healthy (~10fps, 20–30ms
  each) right up to the stall, so you can't see it coming.
  *(This supersedes the earlier "~3.1s stall on a ~12.3s cycle" estimate,
  which came from confounded real-session logs.)*
- **The per-frame event-flush does NOT prevent this stall.** A `--no-flush`
  probe run stalled identically (~6s to stall, ~3s duration). The
  around-capture flush still matters for a different reason (draining
  `FILE_ADDED` so it doesn't poison the next session — §6), but it does *not*
  shield this periodic ~6s-timer stall. Correcting the older assumption.

**The clock FREE-RUNS; standby/resume does NOT reset it.** (Corrects an
earlier wrong conclusion.) The first `--standby-cycle` probe always paused
*right after a stall*, so its clean ~6s-after-resume readings were the STALL
resetting the clock, not the resume. The decisive **mid-window** test
(`--work-s`: poll a sub-6s window → standby → resume → measure) came back
**bimodal** — 0.01s or ~6s depending on phase, never a constant ~6s — and
cycles stalled *during* the supposedly-fresh work window, proving the clock
carries across standbys. Nothing we do to polling (standby, resume, rate,
flush) moves it. Only a **completed stall**, a **re-init**, or a **capture**
resets it.

**A real `capture_image` DOES reset it.** The `--capture-cycle` probe (poll →
real shutter+download → resume → measure) never stalled sooner than **~6s
after a capture** (5.99–11.76s, never immediate) and every capture succeeded
— a capture opens a guaranteed ~6s stall-free window.

**What this means for the app (Option A, shipped 2026-07-11):**

- **You cannot dodge the stall by countdown/standby timing** — there is no
  "fresh window" to steer into; the safe gap arrives on the camera's own
  schedule, not ours.
- **You CAN chain shots inside the window a capture opens.** Keep
  `shot_interval_ms + countdown` < ~5s so shot N+1 fires within ~6s of shot
  N's capture. `shot_interval_ms` was dropped 3s→1s for this — it took the
  collage from ~44% of shots stalled (at ~6.3s capture-to-capture) down to
  ~10%.
- **It's a mitigation, not a cure.** Even chained ~4.7s apart, ~10% of
  captures still land in a stall (`--rapid-capture`: 2/20 failed — isolated
  fast `capture_image` `[-1]`, roughly periodic; the reset isn't perfectly
  clean). The **retry-once** policy (`CAPTURE_RETRY_DELAY_S = 1.5s` + its
  re-init, which itself resets the clock) recovers these — the photo is never
  lost, at the cost of a ~4s "processing" pause on the affected shot.
- **First shot / single mode / retakes** have no preceding capture to open a
  window, so they rely on retry-once. Reaching ~100% clean would require
  **decoupling live view** (unproven feasible for gphoto2/M50 — single PTP
  session).
- The first PTP session after a run **that took a photo** is a different
  thing (wedged from boot, §2), not this periodic stall.

**Reverted alternative:** an always-on 15fps idle-polling experiment + a 1.2s
pre-trigger settle (`CAPTURE_SETTLE_S`) — both reverted; the rest-when-idle
watchdog is the better trade-off. Do **not** confuse that heavyweight ~1s
settle with the ~15ms `PREVIEW_RELEASE_SETTLE_S` in §6 — they solve unrelated
problems.

**Public context:** the *general* fragility of sustained gphoto2 live view on
Canon (degrades/stalls over time) is widely reported, but the specific ~6s
timer / ~3s duration is a **local finding on this body**, not publicly
documented — treat the exact numbers as ours.

---

## 4. Camera Widgets: What They Mean, What Not to Touch

Values observed on this body (logged at every init by `camera_widget_info`,
DEBUG level):

| Widget | Observed value | Notes |
|---|---|---|
| `output` | `'MOBILE2'` | **Read-only for us (house rule).** Canon EOS encodes live-view routing/size here; per the [gPhoto remote docs](http://www.gphoto.org/doc/remote/), `PC` is the largest preview size, `MOBILE` second, `MOBILE2` the smallest. It is NOT a stale smartphone connection. Upstream sanctions writing the size values to pick a preview size — untested on this body. Writing `'TFT'` (LCD routing) is **proven** to kill USB previews for the session. |
| `movierecordtarget` | `'SDRAM'` | Normal. |
| `liveviewsize` | `'Small'` | Matches `MOBILE2`. |
| `eosmovieswitch` | `'0'` | Stills mode. |
| `capturetarget` | `'Internal RAM'` | Set by `init()`. Means a captured photo exists **only in camera RAM** until downloaded — a failed download cannot be recovered, the retry re-shoots instead. Correct trade-off for a booth (no SD card wear/cleanup). |
| `autofocusdrive` | set to `0` at init | An **action** widget, not a mode switch: per libgphoto2 (`examples/focus.c`), `1` drives autofocus once (`DoAf`) and `0` cancels an in-progress AF (`CancelAf`). Our init write of `0` is therefore at most a one-shot AF cancel — **not** a persistent "AF disabled" state; the "prevents AF hunting" rationale in the code comment is inherited folklore. Harmless in practice, and this write has shipped since the baseline; most other config writes into the EOS live-view state machine are not safe (`viewfinder=0` breaks the next session). |

---

## 5. The 35-Second "Camera Whine" That Wasn't

A consistent ~35s whistle "from the camera" after every shutter (and after
the attract-screen tap) turned out to be the **speaker/TV amplifier**, not
the camera: a running Web Audio `AudioContext` holds the OS audio output
open even when silent, keeping the amp powered until its ~35s silence
timeout. Fixed by suspending the context when idle
(`frontend/src/utils/sounds.js`): `unlockAudio()` resumes just long enough
to earn the browser gesture unlock, every tone schedules a suspend 1.5s
after it ends.

Lessons that outlived the bug:

- The camera itself was **never** the noise source; all "live view keeps it
  quiet" theories were coincidence artifacts.
- Physical confirmation (unplugging the speaker) beat two days of theory.
- If a mystery noise appears near the camera, **check the audio path first**.

---

## 6. Preview ↔ Capture: Managing the Transition

Live view (MJPEG preview polling) and high-res capture share **one PTP
session and one USB bus**, and on the M50 they interfere: triggering the
shutter while preview polling is active races the stall cycle (§3) and
produces slow or failed first attempts. The transition choreography exists
to keep them apart.

**Single ownership:** all camera USB I/O runs on the **worker thread**.
Captures are never triggered from an HTTP handler — `enqueue_capture()`
just queues a `CAPTURE` command and returns a `job_id`; the worker executes
it between preview iterations. This is what makes the transition safe:
there is never a moment where preview polling and a capture fight over the
bus from two threads.

**The capture is authoritative over live view (2026-07-10 fix).** `standby()`
alone is only *advisory* — it clears `_preview_allowed`, but
`resume_preview()` (called by `preview_generator()` on every preview-stream
(re)connect) can re-arm it a moment later. If that re-arm lands in the
pre-capture window, the worker starts one more preview grab, rides it into
the ~6s stall (§3), and the queued shutter waits ~3s behind it — this was the
"3rd-collage-shot delay" bug. The fix makes a pending capture a hard gate:

- `enqueue_capture()` sets `_capture_in_progress = True` **and** calls
  `standby()` **before** queuing the job, so preview is suppressed from the
  instant of enqueue.
- the worker skips preview grabs while `_capture_in_progress`;
- `resume_preview()` is a **no-op** while a capture is pending/running, so a
  stream reconnect can't un-park the worker mid-capture.

Once enqueued, nothing can start a grab or re-arm polling until the shot
completes, so the worker services the capture on its next loop (~0.1s) on a
quiet bus. Note this does **not** abort an already-in-flight grab: if the
worker is mid-*stall* when the capture is enqueued, the shutter still waits
behind it. Chaining shots inside the post-capture window (§3, Option A) is
what keeps a stalling grab from being in flight; the two are complementary,
neither is sufficient alone.

**Preview → capture (the shutter sequence):**

1. `enqueue_capture()` gates the capture (above) and standbys — the worker
   stops polling before the CAPTURE command is picked up. `standby()` is
   deliberately gentle: no lock grabbing, no session teardown — the USB bus
   drains while the command sits in the queue (~100–300ms in practice).
2. **Preview-release settle:** the worker sleeps `PREVIEW_RELEASE_SETTLE_S`
   (~15ms) before touching the capture path, so the camera has released the
   live-view USB/PTP state first (gphoto: "preview may not be fully stopped
   when capture is triggered"). Cheap insurance for the ms-scale release
   race; unrelated to the ~6s stall.
3. The worker **flushes pending events** (`wait_for_event` until timeout) —
   leftover live-view events would otherwise stall the trigger.
4. `camera.capture(GP_CAPTURE_IMAGE)` — the `fired` SSE event is emitted
   *just before* the call; `capture()` then blocks ~1.5s and the exposure
   happens somewhere inside that window, so the frontend flash/sound
   slightly **leads** the actual shutter rather than marking it exactly.
5. **Flush again immediately after the trigger** — this drains
   `GP_EVENT_FILE_ADDED` and friends; skipping it lets events pile up and
   poison the next preview session.
6. Download from camera RAM (`file_get`), save, emit `completed`.
7. **Never call `capture_preview()` anywhere in this sequence** — on
   hardware it adds a ~3s delay (regression-tested in
   `test_capture_job_flushes_and_avoids_preview`).

**Capture → preview (getting live view back):**

- Nothing resumes automatically. After a capture the worker stays paused
  (`_preview_allowed` stays cleared from the enqueue standby) through REVEAL —
  the camera rests while the guest looks at their photo. Live view returns
  only when a screen that needs it mounts the MJPEG `<img>`:
  `preview_generator()` entry calls `resume_preview()` (and `init()` in a
  thread if disconnected). During the shot itself `resume_preview()` is gated
  to a no-op; it un-gates when the capture completes, so the next screen's
  resume restarts polling normally. (Resume does NOT open a stall-free window —
  only a capture does, §3.)
- No config writes or session dance are needed to go back — the per-frame
  event flush in the worker loop (step 4 above having done its job) is
  sufficient. At fresh-session countdown entries the first frame arrived
  within ~70ms of resume; retake resumes weren't measured at frame
  granularity, but no visible delay was ever observed.
- The retake loop proven in logs: `resume` → frames → `standby`+capture →
  REVEAL (no preview) → RETAKE → `resume` → frames… every cycle clean, no
  re-init needed between shots.

**What pauses preview vs. what resumes it:**

| Trigger | Mechanism |
|---|---|
| Capture enqueued | `enqueue_capture()` → sets `_capture_in_progress` + `standby()` (hard gate until the shot completes) |
| No viewer for 10s | idle watchdog → `standby()` (deferred while `_warmup_failed`, §2) |
| Explicit API | `POST /api/camera/standby` |
| A screen mounts the preview | `preview_generator()` entry → `resume_preview()` (**no-op while a capture is in progress**) |

**Visual side-note (normal, not a fault):** during USB live view the
camera's own LCD is dark; at the moment of capture it lights up briefly and
turns off again. Guests/operators sometimes read this flicker as a glitch —
it's the M50 switching internal modes.

---

## 7. Capture Pipeline Facts

- Healthy capture timeline (from logs): trigger ~1.5s, post-flush ~30ms,
  download ~5ms from RAM, total ~1.6s. Anything over ~2s usually means the
  trigger landed near a live-view stall (§3), not a fault.
- `capture_pending` → `capture_started` should be **tens of ms** when no
  stall is in flight (the capture-authoritative gate, §6). A ~3s
  `pending→started` gap means the worker was mid-stall when the shot was
  enqueued — reduced (not eliminated) by tight shot chaining (§3); retry-once
  absorbs the residual.
- A ~15ms `PREVIEW_RELEASE_SETTLE_S` idle precedes the trigger (§6); invisible
  next to the ~1.6s capture.
- The retry-once policy lives in the backend (Rule: workflow decisions never
  in the frontend). Frontend sees a single `failed` event only if both
  attempts fail. The retry re-triggers the shutter — with `capturetarget =
  Internal RAM` a photo whose download failed is gone (§4), so the retry
  produces a *new* shot, not a recovered one.

---

## 8. Diagnostic Playbook

Logs are JSONL in `logs/backend_<ts>.log` (pytest runs go to a temp dir and
never pollute this folder).

**A healthy launch looks like:**
`camera_init` → `camera_ready` → 5× `camera_widget_info` → `camera_warmup`
("Viewfinder pre-warmed") → `worker_started` → `camera_watchdog` pause ~10s
later.

**A wedged launch (after a run that took photos) looks like:**
`camera_ready` → `camera_warmup_fail` (WARN) → `worker_preview_err` ×N →
`camera_preview_fail` → another `camera_init` → eventually `camera_warmup` OK.
The worker heals it in ~9s: warmup stall (#1) → 1 worker stall (#2) →
`connected=False` → re-init → `camera_warmup` OK (§2). **This is normal self-healing**, not
a fault.

**Worrying signatures:**

- `camera_warmup_fail` repeating across *multiple consecutive* inits →
  the session-poisoned state (§1 golden rules were probably violated, or the
  body needs a battery pull).
- `worker_preview_err` counts climbing past #2–#3 in a healthy session →
  something new; check `camera_widget_info` values against §4's table.
- Captures failing even after the internal retry → check whether preview
  errors immediately precede each `capture_fired`.

**Useful log events:** `camera_warmup_fail`, `worker_preview_err`,
`camera_preview_fail`, `camera_widget_info`, `worker_cycle` (per-frame
timings, DEBUG), `capture_trigger`/`capture_download` timings,
`camera_watchdog`.

`worker_preview_err` now also reports the healthy run before each stall
(`… (healthy run before stall: N frames / N.Ns; stalled cycle Nms)`) — in a
healthy session the first stall after each `resume` should read ~6s.

**Controlled measurement — `backend/tools/preview_stall_probe.py`:** a
standalone probe (not imported by the app) that isolates continuous
`capture_preview()` polling, free of the standby/capture/re-init noise of a
real session. Run it **on the Pi with the booth stopped** (one process owns
the camera); it heals a wedged first session before measuring, and always
releases the camera cleanly (try/finally + SIGTERM handling) so it never
wedges the next run. Flags: `--fps N` (rate sweep — proves the stall is
time- not frame-triggered), `--no-flush` (does the flush help — it doesn't),
`--standby-cycle [--work-s N]` (does standby/resume reset the clock — **NO**;
a mid-window standby leaves it running), `--capture-cycle` (does a real
capture reset it — **YES**, ~6s window after), `--rapid-capture --gap-s N`
(do tightly-chained captures stay clean — mostly, ~10% still fail). This is
how §3 was characterized.

---

## 9. Timeline of Investigations

| Date | What happened | Outcome |
|---|---|---|
| 2026-07-06 | "~35s whistle after shutter" investigated as a camera issue; camera-side experiments (viewfinder=0, trickle/15fps idle polling, settle pause, init-time session rebuild, `output='TFT'` write) | All reverted — they added latency, capture failures, and in two cases killed previews entirely. Root cause was the speaker amp (§5). Keepers: the sounds.js suspend fix + init-time widget logging. |
| 2026-07-07 | "First preview after launch always fails" investigated | Root-caused to stale PTP session from hard kills (§2). Fixed with fail-fast heal + watchdog viewer fix (`5c5b9c0`), `stop.sh` for clean stops, event-loop offloading for slow USB calls (`1f75959`). Hardware-verified same night. |
| 2026-07-09 / 07-10 | "3rd collage shot / intermittent ~3s pre-shutter delay" investigated | Built `preview_stall_probe.py` and characterized the stall (§3): **time-triggered ~6s cycle, ~3s duration, rate- and flush-independent**, correcting the earlier "~12.3s cycle". Made the capture **authoritative from enqueue** + ~15ms `PREVIEW_RELEASE_SETTLE_S` (§6) and added worker stall instrumentation. (An intermediate "resume resets the clock → ≤5s countdown dodges it" conclusion was later DISPROVEN — see 07-11.) |
| 2026-07-11 | Root cause & fix nailed | Mid-window (`--work-s`) and `--capture-cycle` probes proved standby/resume do **NOT** reset the free-running clock — only a **capture** (or a completed stall / re-init) does, opening a ~6s window. `--rapid-capture` showed chaining shots <~6s apart is a strong mitigation (~44%→~10% stalled) but not a cure. **Option A shipped:** `shot_interval_ms` 3s→1s so collage shots chain inside the window; residual ~10% handled by retry-once; first/single/retake shots rely on retry. ~100% would need decoupling live view (unproven for gphoto2/M50). |
| 2026-07-11 | Startup heal characterized & sped up | `preview_stall_probe.py --heal-probe` (3 strategies, multiple runs) proved the wedge clears **only** via (1) a `capture_image` — instant, but audible shutter, so not used at startup — or (2) ~2 stalls of polling **then** an `exit()+init()`. Polling alone never heals (27s/9 stalls); a re-init before ~2 stalls of priming never heals (why the inline-heal experiment failed, reverted). Root cause also corrected: the wedge is caused by a real `capture_image` and **survives a clean `exit()`** (not the old "hard-kill" story). **Optimization shipped:** `error_limit` 2→1 when `_warmup_failed` (warmup grab = stall #1, so 1 worker stall = the 2 needed) → heal ~12s→~9s. Also dropped a no-op shutdown `output='Off'` experiment. |

Full experimental history is preserved locally in the
`backup/whine-investigation` branch.

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

## 1. The Golden Rules (do / don't)

| Rule | Why |
|---|---|
| **Never write the `output` widget** (house rule) | Writing `'TFT'` kills every preview for the session — proven here. Upstream does sanction writing `PC`/`MOBILE`/`MOBILE2` to select a preview size, but that's untested on this body and we don't need it. `MOBILE2` as its value is **benign** — see §4. |
| **Never set `viewfinder=0`** | Breaks the *next* preview session with persistent `[-1]` until re-init. |
| **Never do an in-place exit+reconnect inside `init()`** right after a failed warmup | It poisons **every subsequent session** too (commits `7237e7a`/`832ab8e`, reverted in `a4f4d51`). Only the worker-cascade heal works: `connected=False` → worker loop re-enters `init()`, which exits the old handle first. |
| **Flush events (`wait_for_event`) around every capture** | The post-trigger flush drains `GP_EVENT_FILE_ADDED`; skip it and events pile up and poison the next preview session. (Note: the per-frame flush does **not** prevent the periodic ~6s live-view stall — measured, §3.) |
| **Chain multi-shot captures tight (`shot_interval_ms + countdown` < ~5s)** | A *capture* opens a ~6s stall-free window (§3); the next shot must fire inside it. `shot_interval_ms` is 1s for this. Standby does NOT open a window — only a capture (or a completed stall / re-init) resets the clock. |
| **Don't rely on countdown/standby timing to dodge the stall** | The ~6s stall clock free-runs on camera time; standby/resume/rate/flush do NOT reset it (§3, disproven the earlier ≤5s idea). |
| **Stop the booth with `stop.sh`, not a hard kill** | SIGTERM lets `camera.exit()` close the PTP session. A hard kill leaves stale live-view state that wedges the next launch's first session (§2). |
| **Prefer letting the camera rest (standby/watchdog) over always-on polling** | Sustained polling exposes the periodic stall (§3); resting resets the clock for the next shot. |
| **Mistrust ear-based localization of noises near the camera** | The famous "camera whine" came from the speaker amplifier (§5). |

Priorities when trading off (per operator): **shutter latency and capture
reliability outweigh idle-time niceties.**

---

## 2. The Wedged First Session

**Symptom:** after every app launch that followed a hard kill or power cut
(which was *every* launch until `stop.sh` existed), the first PTP session has
broken live view — `camera.init()` succeeds, config reads work fine (widget
dump prints), but **every** `capture_preview()` blocks ~3.1s and returns
`[-1] Unspecified error`.

**Root cause** (best-supported explanation — the timeline below is logged,
the mechanism inside the camera is inference backed by the battery-pull and
clean-exit correlations): the previous booth process died without `camera.exit()`
(power cut or hard kill — `main.py`'s SIGTERM handler never got to run). The
M50 retains stale remote/live-view state from that abandoned session, and
that state wedges live view in the *next* session. Only cleanly **exiting the
wedged session itself** resets the camera — which is why the second session
in the same run was always healthy, and why a battery pull also cleared it.

**How it's handled (commit `5c5b9c0`, hardware-verified 2026-07-07):**

- `init()`'s warmup preview failing is the reliable wedge signature → sets
  `_warmup_failed` and logs `camera_warmup_fail` (WARN).
- While `_warmup_failed`: the worker trips the disconnect cascade after
  **2** consecutive errors instead of 6, and the idle watchdog **defers
  standby** so the heal isn't stranded waiting for a viewer.
- The heal is the normal worker path: `connected=False` → worker re-enters
  `init()` → old handle exited → fresh session → warmup succeeds.
- Result: heal completes **~12s after boot**, on the attract screen, before
  a guest ever taps. First countdown gets frames within ~70ms.
- Additionally, `preview_generator()` refreshes `_last_preview_request` on
  **every poll slice**, so an attached viewer staring at a black stream
  counts as a preview request — previously the watchdog judged them absent
  after 10s and paused the worker mid-heal.
- The error counter resets when the cascade trips, so a healed session
  doesn't inherit a hair trigger where one transient stall re-disconnects.

**Prevention:** `stop.sh` (SIGTERM → `camera.exit()`). A launch after a clean
stop shows `camera_warmup` succeeding on the very first init.

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
- The first PTP session after an *unclean previous session* is a different
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

**A wedged launch (after an unclean stop) looks like:**
`camera_ready` → `camera_warmup_fail` (WARN) → 2× `worker_preview_err` →
`camera_preview_fail` → second `camera_init` → `camera_warmup` OK.
All within ~12s of boot. **This is normal self-healing**, not a fault.

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

Full experimental history is preserved locally in the
`backup/whine-investigation` branch.

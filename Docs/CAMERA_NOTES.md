# CAMERA_NOTES — Canon EOS M50 Field Knowledge

Hard-won facts about how **this specific camera body** behaves over USB/PTP
(gphoto2/libgphoto2), gathered during the July 2026 investigations
("35s whine" saga and the "first preview always fails" fix). Everything here
is **log-proven on the booth hardware** unless marked otherwise. Read this
before touching `backend/camera_service.py`.

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
| **Always flush events (`wait_for_event`) between preview frames and around capture** | Without it the M50's event buffer overflows and the camera freezes ~3s with `[-1]`. |
| **Stop the booth with `stop.sh`, not a hard kill** | SIGTERM lets `camera.exit()` close the PTP session. A hard kill leaves stale live-view state that wedges the next launch's first session (§2). |
| **Prefer letting the camera rest (standby/watchdog) over always-on polling** | Sustained polling exposes a periodic stall cycle that degrades capture reliability (§3). |
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

- Under continuous `capture_preview()` polling (observed at both tested
  rates, ~2.5fps and 15fps), the M50 exhibits
  **periodic ~3.1s `[-1]` stalls on a ~12.3s cycle**. Healthy sessions show
  these as isolated `worker_preview_err #1` entries that never cascade.
- A **shutter trigger landing inside a stall runs slow or fails** the first
  attempt. Observed: capture totals of ~1.6s normally vs ~2.1–2.6s when the
  trigger followed a preview error. The capture retry-once policy
  (`CAPTURE_RETRY_DELAY_S = 1.5s`) absorbs outright failures — guests see
  nothing.
- During the investigation, an **always-on 15fps idle polling** experiment
  made first capture attempts fail regularly; a 1.2s pre-trigger settle pause
  (`CAPTURE_SETTLE_S`) shielded captures. Both were **reverted** — the
  watchdog/standby design (camera rests when nobody watches) is the better
  trade-off. If always-on polling is ever reintroduced, bring the settle
  pause back with it.
- The first PTP session after an *unclean previous session* sometimes
  presents as "half-working": warmup fails, previews stall, then heal — this
  is §2, not a random hardware fault.

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

**Preview → capture (the shutter sequence):**

1. `enqueue_capture()` immediately calls `standby()` — clears
   `_preview_allowed`, so the worker stops polling **before** the CAPTURE
   command is even picked up. `standby()` is deliberately gentle: no lock
   grabbing, no session teardown — the USB bus simply drains while the
   command sits in the queue (~100–300ms in practice).
2. The worker picks up the job and **flushes pending events**
   (`wait_for_event` until timeout) — leftover live-view events from the
   standby period would otherwise stall the trigger.
3. `camera.capture(GP_CAPTURE_IMAGE)` — the `fired` SSE event is emitted
   *just before* the call; `capture()` then blocks ~1.5s and the exposure
   happens somewhere inside that window, so the frontend flash/sound
   slightly **leads** the actual shutter rather than marking it exactly.
4. **Flush again immediately after the trigger** — this drains
   `GP_EVENT_FILE_ADDED` and friends; skipping it lets events pile up and
   poison the next preview session.
5. Download from camera RAM (`file_get`), save, emit `completed`.
6. **Never call `capture_preview()` anywhere in this sequence** — on
   hardware it adds a ~3s delay (regression-tested in
   `test_capture_job_flushes_and_avoids_preview`).

**Capture → preview (getting live view back):**

- Nothing resumes automatically. After a capture the worker stays paused
  (`_preview_allowed` cleared) through REVEAL — the camera rests while the
  guest looks at their photo. Live view returns only when a screen that
  needs it mounts the MJPEG `<img>`: `preview_generator()` entry calls
  `resume_preview()` (and `init()` in a thread if disconnected).
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
| Capture enqueued | `enqueue_capture()` → `standby()` |
| No viewer for 10s | idle watchdog → `standby()` (deferred while `_warmup_failed`, §2) |
| Explicit API | `POST /api/camera/standby` |
| A screen mounts the preview | `preview_generator()` entry → `resume_preview()` |

**Visual side-note (normal, not a fault):** during USB live view the
camera's own LCD is dark; at the moment of capture it lights up briefly and
turns off again. Guests/operators sometimes read this flicker as a glitch —
it's the M50 switching internal modes.

---

## 7. Capture Pipeline Facts

- Healthy capture timeline (from logs): trigger ~1.5s, post-flush ~30ms,
  download ~5ms from RAM, total ~1.6s. Anything over ~2s usually means the
  trigger landed near a live-view stall (§3), not a fault.
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

---

## 9. Timeline of Investigations

| Date | What happened | Outcome |
|---|---|---|
| 2026-07-06 | "~35s whistle after shutter" investigated as a camera issue; camera-side experiments (viewfinder=0, trickle/15fps idle polling, settle pause, init-time session rebuild, `output='TFT'` write) | All reverted — they added latency, capture failures, and in two cases killed previews entirely. Root cause was the speaker amp (§5). Keepers: the sounds.js suspend fix + init-time widget logging. |
| 2026-07-07 | "First preview after launch always fails" investigated | Root-caused to stale PTP session from hard kills (§2). Fixed with fail-fast heal + watchdog viewer fix (`5c5b9c0`), `stop.sh` for clean stops, event-loop offloading for slow USB calls (`1f75959`). Hardware-verified same night. |

Full experimental history is preserved locally in the
`backup/whine-investigation` branch.

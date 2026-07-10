#!/usr/bin/env python3
"""
Preview-stall probe — standalone diagnostic for the Canon M50 ~3.1s [-1] stall.

WHY: In real sessions the stall data is confounded by standby/resume, captures,
and re-inits between every polling window, so we can't tell whether the stall is
triggered by (a) a number of preview frames, (b) elapsed polling time, or (c) the
camera's own internal clock. This script isolates ONE variable: continuous
capture_preview() polling. No standby, no capture, no re-init. It mirrors the
app's exact camera calls (see backend/camera_service.py: init() + the worker's
event-flush-then-capture_preview loop) so results transfer.

WHAT IT MEASURES: for every stall (a preview grab that takes > --stall-ms or
raises), it records frames-since-previous-stall and seconds-since-previous-stall.
If frames-between-stalls is ~constant across runs at different --fps, the trigger
is frame-count. If seconds-between-stalls is ~constant across rates, it's time.

IMPORTANT: needs exclusive USB access to the camera — STOP the booth app first
(only one process can hold the camera). Run on the Pi where gphoto2 + the camera
live, not on the Windows dev box.

EXPERIMENT MATRIX (run each ~2-3 min):
  python3 backend/tools/preview_stall_probe.py --fps 10 --duration 180
  python3 backend/tools/preview_stall_probe.py --fps 5  --duration 180
  python3 backend/tools/preview_stall_probe.py --fps 15 --duration 180
      -> frame-count trigger if frames/stall is stable across fps;
         time trigger if seconds/stall is stable across fps.
  python3 backend/tools/preview_stall_probe.py --fps 10 --duration 180 --no-flush
      -> tests whether the wait_for_event flush actually reduces stalls
         (camera_service.py claims it prevents the freeze).
  python3 backend/tools/preview_stall_probe.py --standby-cycle --pause-s 3 --duration 120
  python3 backend/tools/preview_stall_probe.py --standby-cycle --pause-s 6 --duration 120
      -> does standby->resume reset the ~6s stall clock? NOTE: this variant
         always pauses AFTER a stall (clock already at 0), so it can't tell a
         real reset from "the stall reset it + the pause held". Use --work-s
         for the decisive test.
  python3 backend/tools/preview_stall_probe.py --standby-cycle --work-s 3 --pause-s 3 --duration 120
      -> MID-WINDOW test (mimics the app's pose-standby at ~3s into a 3s
         countdown): poll --work-s (a sub-6s window, before any stall), THEN
         standby, resume, and measure seconds-to-first-stall. ~6s => a
         mid-window standby resets the clock (the app dodge is viable);
         ~(6-work_s) => it does NOT reset, the clock runs continuously and the
         dodge is dead (decouple live view). This is the one that matters.
  python3 backend/tools/preview_stall_probe.py --capture-cycle --work-s 3 --duration 120
      -> does a REAL capture (which exits live view) reset the clock? Polls,
         fires a real shutter+download, resumes, measures secs-to-first-stall.
         Consistently ~6s => capture resets it (a timing fix path exists);
         immediate/phase-dependent => it doesn't (only decouple/accept).
         NOTE: fires the physical shutter each cycle.

Each run appends a per-frame CSV (--out) and prints a summary at the end
(also on Ctrl+C).
"""
import argparse
import csv
import signal
import sys
import time

try:
    import gphoto2 as gp
except ImportError:
    sys.exit("python-gphoto2 not installed / not on this platform. Run on the Pi.")


def _configure(cam):
    """Mirror CameraService.init() config: capturetarget=RAM, autofocusdrive=0."""
    try:
        cfg = cam.get_config()
        ok, w = gp.gp_widget_get_child_by_name(cfg, "capturetarget")
        if ok >= gp.GP_OK:
            for i in range(w.count_choices()):
                c = w.get_choice(i)
                if "RAM" in c or "Internal" in c:
                    w.set_value(c)
                    break
            cam.set_config(cfg)
    except Exception as e:
        print(f"[warn] capturetarget: {e}")
    try:
        cfg = cam.get_config()
        ok, w = gp.gp_widget_get_child_by_name(cfg, "autofocusdrive")
        if ok >= gp.GP_OK:
            w.set_value(0)
            cam.set_config(cfg)
    except Exception as e:
        print(f"[warn] autofocusdrive: {e}")


def _warmup_ok(cam, tries=3):
    """A wedged live-view session (stale state left by a prior unclean kill)
    makes every capture_preview stall ~3s then throw [-1], even though config
    reads work. Try a few quick grabs; return True as soon as one yields a frame.
    """
    for _ in range(tries):
        try:
            cf = cam.capture_preview()
            cf.get_data_and_size()
            return True
        except Exception:
            time.sleep(0.2)
    return False


def get_healthy_camera(max_heals=8, settle_s=1.5):
    """Return a camera whose live view actually produces frames.

    The first PTP session after an unclean app kill is wedged (see the memory /
    camera_service.py). The app's WORKING heal is to exit the stale handle and
    re-init a fresh session; we replicate that here, retrying until a warmup
    frame lands, so the measurement loop starts from a healthy baseline instead
    of recording a wall of wedged-session stalls.
    """
    cam = None
    for attempt in range(1, max_heals + 1):
        if cam is not None:
            try:
                cam.exit()
            except Exception:
                pass
            time.sleep(settle_s)  # let USB settle between heals
        try:
            cam = gp.Camera()
            cam.init()
        except Exception as e:
            print(f"[heal {attempt}/{max_heals}] init failed: {e} "
                  "(is the booth app still running, or the camera unplugged?)")
            continue
        _configure(cam)
        if _warmup_ok(cam):
            if attempt > 1:
                print(f"camera healed after {attempt} attempts.")
            return cam
        print(f"[heal {attempt}/{max_heals}] session wedged "
              "(warmup preview stalls [-1]); exit + re-init...")
    if cam is not None:
        try:
            cam.exit()  # don't leave the last wedged handle open for the next run
        except Exception:
            pass
    sys.exit("Could not heal the camera to a healthy live-view session. "
             "Unplug/replug the camera (or pull the battery) and retry.")


def drain_events(cam):
    """Same flush the worker does before each preview grab (camera_service.py)."""
    try:
        evt_type, _ = cam.wait_for_event(10)
        while evt_type != gp.GP_EVENT_TIMEOUT:
            evt_type, _ = cam.wait_for_event(5)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fps", type=float, default=10.0, help="target preview poll rate")
    ap.add_argument("--duration", type=float, default=180.0, help="run seconds")
    ap.add_argument("--stall-ms", type=float, default=1000.0,
                    help="a preview grab slower than this counts as a stall")
    ap.add_argument("--flush", dest="flush", action="store_true", default=True,
                    help="drain camera events before each grab (default, matches app)")
    ap.add_argument("--no-flush", dest="flush", action="store_false")
    ap.add_argument("--out", default="preview_stall_probe.csv", help="per-frame CSV path")
    ap.add_argument("--standby-cycle", action="store_true",
                    help="resume->poll-until-first-stall->standby(pause) repeatedly, to test "
                         "whether resume resets the ~6s stall clock")
    ap.add_argument("--poll-s", type=float, default=12.0,
                    help="[standby-cycle] max seconds to poll each burst before giving up on a stall")
    ap.add_argument("--pause-s", type=float, default=3.0,
                    help="[standby-cycle] seconds to stop polling (simulated standby) between bursts")
    ap.add_argument("--work-s", type=float, default=0.0,
                    help="[standby-cycle] if >0, MID-WINDOW mode: poll this long (a sub-6s "
                         "healthy window, like the app's countdown) BEFORE the standby, to test "
                         "whether a standby placed before the stall resets the clock. "
                         "[capture-cycle] seconds to poll before each real capture (default 3)")
    ap.add_argument("--capture-cycle", action="store_true",
                    help="poll --work-s → fire a REAL capture_image+download (exits live view) "
                         "→ resume → measure secs-to-first-stall. Tests whether a real capture "
                         "resets the ~6s clock. FIRES THE SHUTTER for real.")
    args = ap.parse_args()

    interval = 1.0 / args.fps if args.fps > 0 else 0.0
    stall_s = args.stall_ms / 1000.0

    print(f"init camera... (flush={args.flush}, fps={args.fps}, {args.duration}s)")
    cam = get_healthy_camera()
    print("camera ready (healthy warmup frame). polling. Ctrl+C to stop early.\n")

    state = {"frames": 0, "frames_since_stall": 0, "t_prev_stall": time.monotonic()}
    stalls = []      # continuous: (seq, frames_since_prev, secs_since_prev, dur_s, raised)
    cycles = []      # standby-cycle: (cyc, pause_before_s, frames_to_stall, secs_to_stall, dur_s, hit)
    t_start = time.monotonic()
    csvf = open(args.out, "a", newline="")
    w = csv.writer(csvf)
    if csvf.tell() == 0:
        w.writerow(["seq", "t_rel_s", "preview_ms", "status"])

    def grab():
        """One preview grab (with optional flush). Returns (raised, dur_s, g0)."""
        if args.flush:
            drain_events(cam)
        g0 = time.monotonic()
        raised = False
        try:
            cf = cam.capture_preview()
            _ = cf.get_data_and_size()
        except Exception:
            raised = True
        return raised, time.monotonic() - g0, g0

    def pace(g0):
        s = interval - (time.monotonic() - g0)
        if s > 0:
            time.sleep(s)

    def continuous_loop():
        while time.monotonic() - t_start < args.duration:
            raised, dur_s, g0 = grab()
            if raised or dur_s >= stall_s:
                secs_since = g0 - state["t_prev_stall"]
                stalls.append((state["frames"], state["frames_since_stall"], secs_since, dur_s, raised))
                print(f"STALL @frame {state['frames']}: {state['frames_since_stall']} frames / "
                      f"{secs_since:.1f}s since last stall, took {dur_s:.2f}s, raised={raised}")
                state["frames_since_stall"] = 0
                state["t_prev_stall"] = g0
                w.writerow([state["frames"], g0 - t_start, dur_s * 1000, "err" if raised else "slow"])
            else:
                state["frames"] += 1
                state["frames_since_stall"] += 1
                w.writerow([state["frames"], g0 - t_start, dur_s * 1000, "ok"])
            pace(g0)

    def standby_cycle_loop():
        """Resume (poll) until the first stall, then standby (stop polling) for
        --pause-s, and repeat. Measures seconds-from-resume-to-first-stall each
        cycle. If it stays ~constant (~6s) regardless of the pause, resume RESETS
        the camera's stall clock (a timing/dodge fix is viable). If it drifts,
        the clock is absolute and dodging won't work. The app's standby is
        exactly this: stop calling capture_preview — no camera command is sent."""
        cyc = 0
        while time.monotonic() - t_start < args.duration:
            cyc += 1
            pause_before = 0.0 if cyc == 1 else args.pause_s
            t_resume = time.monotonic()
            fcount = 0
            hit = None
            while (time.monotonic() - t_resume < args.poll_s
                   and time.monotonic() - t_start < args.duration):
                raised, dur_s, g0 = grab()
                if raised or dur_s >= stall_s:
                    hit = (fcount, g0 - t_resume, dur_s)
                    w.writerow([state["frames"], g0 - t_start, dur_s * 1000,
                                "err" if raised else "slow"])
                    break
                state["frames"] += 1
                fcount += 1
                w.writerow([state["frames"], g0 - t_start, dur_s * 1000, "ok"])
                pace(g0)
            if hit is None:
                cycles.append((cyc, pause_before, fcount, None, None, False))
                print(f"cycle {cyc}: NO stall in {args.poll_s:.0f}s ({fcount} frames) "
                      f"[pause before: {pause_before:.1f}s]")
            else:
                f1, s1, d1 = hit
                cycles.append((cyc, pause_before, f1, s1, d1, True))
                print(f"cycle {cyc}: resume -> first stall in {s1:.2f}s / {f1} frames "
                      f"[pause before: {pause_before:.1f}s]")
            pend = time.monotonic() + args.pause_s
            while time.monotonic() < pend and time.monotonic() - t_start < args.duration:
                time.sleep(0.05)

    def midwindow_cycle_loop():
        """Does a standby placed BEFORE the stall (mid healthy window, like the
        app's pose-standby at ~3s into a 3s countdown) reset the ~6s clock?

        Per cycle: [fresh window] poll --work-s (a sub-6s window, no stall yet)
        → standby --pause-s → resume → poll until first stall, record
        secs-to-stall. Each cycle's fresh window starts right after the previous
        cycle's stall (a known reset), so the only variable under test is the
        mid-window standby.

        Read the aggregate:
          secs-to-stall ~= 6s      → the mid-window standby DID reset the clock
                                       → the app's pose-standby dodge is viable
          secs-to-stall ~= 6-work_s → it did NOT reset (clock runs continuously)
                                       → dodge is dead; decouple live view
        """
        cyc = 0
        while time.monotonic() - t_start < args.duration:
            cyc += 1
            # Phase A — fresh window: poll work_s (should be stall-free).
            t_a = time.monotonic()
            stalled_in_work = False
            while (time.monotonic() - t_a < args.work_s
                   and time.monotonic() - t_start < args.duration):
                raised, dur_s, g0 = grab()
                if raised or dur_s >= stall_s:
                    stalled_in_work = True
                    w.writerow([state["frames"], g0 - t_start, dur_s * 1000,
                                "err" if raised else "slow"])
                    break
                state["frames"] += 1
                w.writerow([state["frames"], g0 - t_start, dur_s * 1000, "ok"])
                pace(g0)
            # Phase B — mid-window standby (stop polling before any stall).
            pend = time.monotonic() + args.pause_s
            while time.monotonic() < pend and time.monotonic() - t_start < args.duration:
                time.sleep(0.05)
            # Phase C — resume, measure time to first stall.
            t_c = time.monotonic()
            fcount = 0
            hit = None
            while (time.monotonic() - t_c < args.poll_s
                   and time.monotonic() - t_start < args.duration):
                raised, dur_s, g0 = grab()
                if raised or dur_s >= stall_s:
                    hit = (fcount, g0 - t_c, dur_s)
                    w.writerow([state["frames"], g0 - t_start, dur_s * 1000,
                                "err" if raised else "slow"])
                    break
                state["frames"] += 1
                fcount += 1
                w.writerow([state["frames"], g0 - t_start, dur_s * 1000, "ok"])
                pace(g0)
            flag = "  [!! stalled during the work phase]" if stalled_in_work else ""
            if hit is None:
                cycles.append((cyc, args.pause_s, fcount, None, None, False))
                print(f"cycle {cyc}: work {args.work_s:.1f}s → standby "
                      f"{args.pause_s:.1f}s → resume → NO stall in {args.poll_s:.0f}s{flag}")
            else:
                f1, s1, d1 = hit
                cycles.append((cyc, args.pause_s, f1, s1, d1, True))
                print(f"cycle {cyc}: work {args.work_s:.1f}s → standby "
                      f"{args.pause_s:.1f}s → resume → first stall in {s1:.2f}s{flag}")

    def do_capture():
        """Fire a real shutter + download — the operation that exits live view
        (mirrors _attempt_capture minus saving to disk). Returns (ok, seconds)."""
        t0 = time.monotonic()
        try:
            drain_events(cam)                       # pre-capture flush (like the app)
            fp = cam.capture(gp.GP_CAPTURE_IMAGE)
            drain_events(cam)                       # post-trigger flush
            cf = cam.file_get(fp.folder, fp.name, gp.GP_FILE_TYPE_NORMAL)
            cf.get_data_and_size()                  # download from RAM, discard
            return True, time.monotonic() - t0
        except Exception as e:
            print(f"    capture failed: {e}")
            return False, time.monotonic() - t0

    def capture_cycle_loop():
        """Does a REAL capture (which exits live view) reset the ~6s stall clock?
        Per cycle: poll --work-s to establish live view → fire a real capture →
        resume → measure secs-to-first-stall. Consistently ~6s => a capture
        RESETS the clock (a timing fix path exists); immediate/phase-dependent
        => it does not (only decouple/accept)."""
        work = args.work_s if args.work_s > 0 else 3.0
        cyc = 0
        while time.monotonic() - t_start < args.duration:
            cyc += 1
            # Phase A: poll to establish a live-view session.
            t_a = time.monotonic()
            while (time.monotonic() - t_a < work
                   and time.monotonic() - t_start < args.duration):
                raised, dur_s, g0 = grab()
                is_stall = raised or dur_s >= stall_s
                w.writerow([state["frames"], g0 - t_start, dur_s * 1000,
                            "err" if is_stall else "ok"])
                if not is_stall:
                    state["frames"] += 1
                pace(g0)
            if time.monotonic() - t_start >= args.duration:
                break
            # Phase B: real capture (exits live view).
            cap_ok, cap_dur = do_capture()
            # Phase C: resume, measure time to first stall.
            t_c = time.monotonic()
            fcount = 0
            hit = None
            while (time.monotonic() - t_c < args.poll_s
                   and time.monotonic() - t_start < args.duration):
                raised, dur_s, g0 = grab()
                if raised or dur_s >= stall_s:
                    hit = (fcount, g0 - t_c, dur_s)
                    w.writerow([state["frames"], g0 - t_start, dur_s * 1000,
                                "err" if raised else "slow"])
                    break
                state["frames"] += 1
                fcount += 1
                w.writerow([state["frames"], g0 - t_start, dur_s * 1000, "ok"])
                pace(g0)
            capname = "ok" if cap_ok else "FAIL"
            if hit is None:
                cycles.append((cyc, cap_dur, fcount, None, None, False))
                print(f"cycle {cyc}: work {work:.1f}s → capture({capname} {cap_dur:.2f}s) "
                      f"→ resume → NO stall in {args.poll_s:.0f}s")
            else:
                f1, s1, d1 = hit
                cycles.append((cyc, cap_dur, f1, s1, d1, True))
                print(f"cycle {cyc}: work {work:.1f}s → capture({capname} {cap_dur:.2f}s) "
                      f"→ resume → first stall in {s1:.2f}s")

    def print_summary():
        dur = time.monotonic() - t_start
        print("\n===== SUMMARY =====")
        if args.capture_cycle:
            work = args.work_s if args.work_s > 0 else 3.0
            print(f"ran {dur:.1f}s, capture-cycle (work {work:.1f}s), "
                  f"{state['frames']} good frames, {len(cycles)} cycles")
            print(f"{'cycle':>5} {'capture_s':>10} {'frames->stall':>14} "
                  f"{'secs_capture->stall':>20} {'stall_dur':>10} {'stalled':>8}")
            for (c, cd, fts, sts, dd, hit) in cycles:
                sts_s = f"{sts:.2f}" if sts is not None else "-"
                dd_s = f"{dd:.2f}" if dd is not None else "-"
                print(f"{c:>5} {cd:>10.2f} {fts:>14} {sts_s:>20} {dd_s:>10} {str(hit):>8}")
            got = [c[3] for c in cycles[1:] if c[5]]  # skip cycle 1 (warmup-biased)
            if got:
                print(f"\nsecs capture->first-stall (cycles 2+): min={min(got):.2f} "
                      f"max={max(got):.2f} mean={sum(got)/len(got):.2f}")
                print("  ~6s consistently => a real capture RESETS the clock (timing fix path exists)")
                print("  immediate/varies => capture does NOT reset it (only decouple/accept)")
        elif args.standby_cycle:
            mode = (f"mid-window (work {args.work_s:.1f}s / pause {args.pause_s:.0f}s)"
                    if args.work_s > 0
                    else f"poll<={args.poll_s:.0f}s / pause {args.pause_s:.0f}s")
            print(f"ran {dur:.1f}s, standby-cycle [{mode}], {state['frames']} good "
                  f"frames, {len(cycles)} cycles")
            print(f"{'cycle':>5} {'pause_before':>13} {'frames->stall':>14} "
                  f"{'secs_resume->stall':>19} {'stall_dur':>10} {'stalled':>8}")
            for (c, pb, fts, sts, dd, hit) in cycles:
                sts_s = f"{sts:.2f}" if sts is not None else "-"
                dd_s = f"{dd:.2f}" if dd is not None else "-"
                print(f"{c:>5} {pb:>13.1f} {fts:>14} {sts_s:>19} {dd_s:>10} {str(hit):>8}")
            got = [c[3] for c in cycles[1:] if c[5]]  # skip cycle 1 (warmup-biased)
            if got:
                print(f"\nsecs resume->first-stall (cycles 2+): min={min(got):.2f} "
                      f"max={max(got):.2f} mean={sum(got)/len(got):.2f}")
                if args.work_s > 0:
                    print(f"  ~6s          => the mid-window standby RESETS the clock "
                          f"(app pose-standby dodge viable)")
                    print(f"  ~{max(6 - args.work_s, 0):.0f}s (6-work) => it did NOT reset; the clock "
                          f"runs continuously (dodge dead → decouple live view)")
                else:
                    print("  ~constant ~6s => resume RESETS the stall clock (timing dodge viable)")
                    print("  drifts/varies => clock is absolute (dodge won't work; decouple live view)")
        else:
            print(f"ran {dur:.1f}s, {state['frames']} good frames "
                  f"({state['frames'] / dur if dur else 0:.1f} fps), {len(stalls)} stalls")
            if stalls:
                print(f"{'stall#':>6} {'frames_since_prev':>18} {'secs_since_prev':>16} "
                      f"{'stall_dur_s':>12} {'raised':>7}")
                for i, (seq, fsp, ssp, dur_s, raised) in enumerate(stalls, 1):
                    print(f"{i:>6} {fsp:>18} {ssp:>16.2f} {dur_s:>12.2f} {str(raised):>7}")
                fsps = [s[1] for s in stalls[1:]]  # skip first (warmup-biased)
                ssps = [s[2] for s in stalls[1:]]
                durs = [s[3] for s in stalls]
                if fsps:
                    print(f"\nframes-between-stalls: min={min(fsps)} max={max(fsps)} "
                          f"mean={sum(fsps)/len(fsps):.0f}")
                    print(f"secs-between-stalls:   min={min(ssps):.1f} max={max(ssps):.1f} "
                          f"mean={sum(ssps)/len(ssps):.1f}")
                print(f"stall duration:        min={min(durs):.2f} max={max(durs):.2f} "
                      f"mean={sum(durs)/len(durs):.2f}")

    # Plain `kill` (SIGTERM) must shut down as cleanly as Ctrl+C — a hard exit
    # that skips cam.exit() is exactly what leaves the PTP session wedged for
    # the next run. SIGINT already raises KeyboardInterrupt; make SIGTERM do the
    # same so both land in the finally below.
    def _sigterm(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _sigterm)

    try:
        if args.capture_cycle:
            capture_cycle_loop()
        elif args.standby_cycle and args.work_s > 0:
            midwindow_cycle_loop()
        elif args.standby_cycle:
            standby_cycle_loop()
        else:
            continuous_loop()
    except KeyboardInterrupt:
        print("\ninterrupted.")
    finally:
        # ALWAYS release the camera and flush the CSV, however we leave (normal
        # end, Ctrl+C, SIGTERM, or an unexpected error) — otherwise we wedge the
        # session for the next test, which is the very bug we're chasing.
        print_summary()
        try:
            csvf.flush()
            csvf.close()
        except Exception:
            pass
        try:
            cam.exit()
            print("camera released cleanly (exit()).")
        except Exception as e:
            print(f"[warn] camera exit failed: {e}")


if __name__ == "__main__":
    main()

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
    args = ap.parse_args()

    interval = 1.0 / args.fps if args.fps > 0 else 0.0
    stall_s = args.stall_ms / 1000.0

    print(f"init camera... (flush={args.flush}, fps={args.fps}, {args.duration}s)")
    cam = get_healthy_camera()
    print("camera ready (healthy warmup frame). polling. Ctrl+C to stop early.\n")

    frames = 0
    stalls = []                       # (seq, frames_since_prev, secs_since_prev, dur_s, raised)
    frames_since_stall = 0
    t_prev_stall = time.monotonic()
    t_start = time.monotonic()
    csvf = open(args.out, "a", newline="")
    w = csv.writer(csvf)
    if csvf.tell() == 0:
        w.writerow(["seq", "t_rel_s", "preview_ms", "status"])

    def print_summary():
        dur = time.monotonic() - t_start
        print("\n===== SUMMARY =====")
        print(f"ran {dur:.1f}s, {frames} good frames "
              f"({frames / dur if dur else 0:.1f} fps), {len(stalls)} stalls")
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
        while time.monotonic() - t_start < args.duration:
            if args.flush:
                drain_events(cam)
            raised = False
            g0 = time.monotonic()
            try:
                cf = cam.capture_preview()
                _ = cf.get_data_and_size()
            except Exception:
                raised = True
            dur_s = time.monotonic() - g0

            if raised or dur_s >= stall_s:
                secs_since = g0 - t_prev_stall
                stalls.append((frames, frames_since_stall, secs_since, dur_s, raised))
                print(f"STALL @frame {frames}: {frames_since_stall} frames / "
                      f"{secs_since:.1f}s since last stall, took {dur_s:.2f}s, raised={raised}")
                frames_since_stall = 0
                t_prev_stall = g0
                w.writerow([frames, g0 - t_start, dur_s * 1000, "err" if raised else "slow"])
            else:
                frames += 1
                frames_since_stall += 1
                w.writerow([frames, g0 - t_start, dur_s * 1000, "ok"])

            sleep = interval - (time.monotonic() - g0)
            if sleep > 0:
                time.sleep(sleep)
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

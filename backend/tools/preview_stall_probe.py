#!/usr/bin/env python3
"""
Camera probe — two live jobs, both about capture reliability on the Canon M50.

Run on the Pi, with the booth STOPPED (only one process can hold the camera).

  1. HEALTH (default) — is live view clean?

        python3 backend/tools/preview_stall_probe.py --duration 60

     Polls capture_preview continuously and reports any stall. On a correct
     install there are ZERO: the camera sustains 60s+ of live view at 60fps.

     Stalls at ~3.0s every ~6s means the venv is on the python-gphoto2 WHEEL
     (see CAMERA_NOTES.md). Fix:

        pip install --force-reinstall --no-binary gphoto2 gphoto2

     `--no-binary gphoto2` lives in backend/requirements.txt to prevent this;
     see CONSTRAINTS.md. This mode is the canary for it, which is why it stays.

  2. --retry-probe — the one real defect left: ~7% of captures fail.

        python3 backend/tools/preview_stall_probe.py --retry-probe --shots 50

     capture_image fires, then returns [-1] ~0.8s later. The cause (AF-S cannot
     lock on a moving subject) and the evidence are in CAMERA_NOTES.md.

     The app survives it, but the failure costs the guest a full re-init before
     the retry. This mode escalates per failure to find out whether that re-init
     is actually needed:

        stage 1  BARE    : wait --bare-delay-s, retry. No re-init.
        stage 2  RE-INIT : only if stage 1 failed — the app's behavior today.

     USE --shots 50. At ~7%, a 20-shot run often yields one failure or none, and
     one lucky bare retry is not evidence.

     THE PENDING TEST — does MF fix it? Set the camera to MF, pre-focus on the
     guest mark, then re-run --retry-probe --shots 50 while waving a hand in
     frame for ALL 50. Zero failures ⇒ AF confirmed as the sole cause.

WHAT THIS FILE USED TO BE. It grew to ~1,700 lines and ten modes characterizing a
"periodic ~3s live-view stall on a ~6s clock" that turned out to be a libgphoto2
2.5.34 bug, not the camera (Docs/CAMERA_NOTES.md has the full account). Those modes
— the standby/capture-clock cycles, the spacing sweeps, the lock-contention rig, the
wedged-session heal probe, the libgphoto2 tracer — all answered questions that are
now settled, so they are gone. `git log` has them if a claim ever needs re-checking.
Companion tool: trace_wall.py, which finds the longest silence in a `gphoto2 --debug`
log (that is how the library was caught).
"""
import argparse
import signal
import sys
import threading
import time

try:
    import gphoto2 as gp
except ImportError:
    sys.exit("python-gphoto2 not installed / not on this platform. Run on the Pi.")


def print_version():
    """The whole stall saga was a library-version bug, so always say which one is
    loaded. If this prints 2.5.34 you are on the wheel and the stall is back."""
    try:
        print("libgphoto2:", gp.gp_library_version(gp.GP_VERSION_VERBOSE)[0])
    except Exception as e:
        print(f"[warn] could not read libgphoto2 version: {e}")


def configure(cam):
    """Mirror CameraService.init(): capturetarget=RAM, autofocusdrive=0."""
    for key, want in (("capturetarget", "RAM"), ("autofocusdrive", 0)):
        try:
            cfg = cam.get_config()
            ok, w = gp.gp_widget_get_child_by_name(cfg, key)
            if ok < gp.GP_OK:
                continue
            if key == "capturetarget":
                for i in range(w.count_choices()):
                    c = w.get_choice(i)
                    if "RAM" in c or "Internal" in c:
                        w.set_value(c)
                        break
            else:
                w.set_value(want)
            cam.set_config(cfg)
        except Exception as e:
            print(f"[warn] {key}: {e}")


def open_camera():
    """Open the camera and prove live view works before measuring anything.

    (This used to be an 8-attempt exit+re-init heal loop, for a wedged first
    session that only happened on the broken library. A plain init works now; if
    the warmup grab fails here, something is genuinely wrong — start with the
    libgphoto2 version printed above.)
    """
    cam = gp.Camera()
    cam.init()
    configure(cam)
    try:
        cam.capture_preview().get_data_and_size()
    except Exception as e:
        sys.exit(f"warmup preview failed ({e}) — camera is not usable. "
                 "If libgphoto2 above is 2.5.34, that is the cause: see the docstring.")
    return cam


def drain_events(cam):
    """The flush the app does around a capture (clears GP_EVENT_FILE_ADDED)."""
    try:
        evt, _ = cam.wait_for_event(10)
        while evt != gp.GP_EVENT_TIMEOUT:
            evt, _ = cam.wait_for_event(5)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--fps", type=float, default=15.0,
                    help="[health] preview poll rate (0 = unpaced, as fast as it will go)")
    ap.add_argument("--duration", type=float, default=60.0, help="[health] run seconds")
    ap.add_argument("--stall-ms", type=float, default=1000.0,
                    help="a preview grab slower than this counts as a stall")
    ap.add_argument("--retry-probe", action="store_true",
                    help="fire real captures and, on each failure, try a BARE retry before "
                         "falling back to the app's re-init. Answers whether the re-init is "
                         "needed. FIRES THE SHUTTER.")
    ap.add_argument("--shots", type=int, default=50, help="[retry-probe] captures to fire")
    ap.add_argument("--gap-s", type=float, default=4.0,
                    help="[retry-probe] seconds of live view between captures")
    ap.add_argument("--bare-delay-s", type=float, default=0.3,
                    help="[retry-probe] pause before the BARE retry. Sweep 0.1/0.3/1.5 — if the "
                         "bare retry only needs more TIME, that is still far cheaper than a re-init")
    ap.add_argument("--reinit-delay-s", type=float, default=1.5,
                    help="[retry-probe] pause before the RE-INIT retry (1.5 = the app's "
                         "CAPTURE_RETRY_DELAY_S, so stage 2 costs what the app costs today)")
    args = ap.parse_args()

    interval = 1.0 / args.fps if args.fps > 0 else 0.0
    stall_s = args.stall_ms / 1000.0

    print_version()
    cam = open_camera()
    print("camera ready (live view produced a frame).\n")
    t_start = time.monotonic()

    stalls = []      # health:      (t_rel, dur_s, secs_since_prev)
    retries = []     # retry-probe: (n, first_ok, recovered_by, first_fail_s, total_s)

    def do_capture():
        """Fire a real shutter + download (mirrors _attempt_capture, minus saving).
        Returns (ok, seconds, err)."""
        t0 = time.monotonic()
        try:
            drain_events(cam)
            fp = cam.capture(gp.GP_CAPTURE_IMAGE)
            drain_events(cam)
            cam.file_get(fp.folder, fp.name, gp.GP_FILE_TYPE_NORMAL).get_data_and_size()
            return True, time.monotonic() - t0, None
        except Exception as e:
            return False, time.monotonic() - t0, str(e)

    def health_loop():
        """Poll live view continuously. Any stall at all is a finding."""
        print(f"polling live view for {args.duration:.0f}s "
              f"(fps={'unpaced' if not interval else args.fps}). Ctrl+C to stop early.\n")
        frames = 0
        t_prev = time.monotonic()
        while time.monotonic() - t_start < args.duration:
            g0 = time.monotonic()
            try:
                cam.capture_preview().get_data_and_size()
                raised = False
            except Exception:
                raised = True
            dur = time.monotonic() - g0
            if raised or dur >= stall_s:
                stalls.append((g0 - t_start, dur, g0 - t_prev))
                print(f"STALL at {g0 - t_start:5.1f}s: took {dur:.2f}s "
                      f"({g0 - t_prev:.1f}s since the last one)")
                t_prev = g0
            else:
                frames += 1
            slack = interval - (time.monotonic() - g0)
            if slack > 0:
                time.sleep(slack)
        return frames

    def retry_probe_loop():
        """Fire captures; on failure, BARE retry first, then the app's re-init."""
        nonlocal cam
        lock = threading.Lock()
        stop = threading.Event()
        capturing = threading.Event()

        def worker():
            # The app's shape: a preview worker in a background thread holding the
            # camera lock. Keeps the camera in the same state a real session does.
            while not stop.is_set():
                if capturing.is_set():
                    time.sleep(0.01)
                    continue
                with lock:
                    if capturing.is_set():
                        continue
                    try:
                        cam.capture_preview().get_data_and_size()
                    except Exception:
                        pass
                time.sleep(interval)

        th = threading.Thread(target=worker, daemon=True, name="probe-preview")
        th.start()
        print(f"firing {args.shots} captures, {args.gap_s:.1f}s of live view between.")
        print(f"on failure: BARE retry after {args.bare_delay_s:.2f}s; only if that fails, "
              f"RE-INIT after {args.reinit_delay_s:.2f}s (what the app does today).\n")

        try:
            for n in range(1, args.shots + 1):
                pend = time.monotonic() + args.gap_s
                while time.monotonic() < pend:
                    time.sleep(0.02)

                capturing.set()
                t0 = time.monotonic()
                with lock:
                    ok, cd, err = do_capture()
                    if ok:
                        print(f"capture {n:>3}: ok ({cd:.2f}s)")
                        retries.append((n, True, None, cd, None))
                        capturing.clear()
                        continue

                    print(f"capture {n:>3}: FAILED after {cd:.2f}s — {err}")
                    time.sleep(args.bare_delay_s)
                    ok2, cd2, err2 = do_capture()
                    if ok2:
                        total = time.monotonic() - t0
                        print(f"           -> BARE retry recovered it ({cd2:.2f}s, "
                              f"{total:.2f}s total). No re-init needed.")
                        retries.append((n, False, "bare", cd, total))
                        capturing.clear()
                        continue

                    print(f"           -> bare retry also failed ({cd2:.2f}s — {err2}); "
                          f"re-initting...")
                    time.sleep(args.reinit_delay_s)
                    try:
                        cam.exit()
                    except Exception:
                        pass
                    try:
                        cam = gp.Camera()
                        cam.init()
                        configure(cam)
                    except Exception as e:
                        print(f"           -> re-init FAILED: {e}")
                        retries.append((n, False, "dead", cd, time.monotonic() - t0))
                        capturing.clear()
                        continue
                    ok3, cd3, err3 = do_capture()
                    total = time.monotonic() - t0
                    if ok3:
                        print(f"           -> RE-INIT retry recovered it ({cd3:.2f}s, "
                              f"{total:.2f}s total).")
                        retries.append((n, False, "reinit", cd, total))
                    else:
                        print(f"           -> STILL failed ({err3}). Photo would be LOST.")
                        retries.append((n, False, "dead", cd, total))
                capturing.clear()
        finally:
            stop.set()
            th.join(timeout=5)

    def print_health_summary(frames):
        dur = time.monotonic() - t_start
        print("\n===== SUMMARY =====")
        print(f"ran {dur:.0f}s, {frames} frames ({frames / dur if dur else 0:.1f} fps), "
              f"{len(stalls)} stalls")
        if not stalls:
            print("\n  CLEAN. Live view is healthy — this is what a correct install looks like.")
            return
        durs = [s[1] for s in stalls]
        gaps = [s[2] for s in stalls[1:]]
        print(f"  stall duration:     min={min(durs):.2f}s max={max(durs):.2f}s "
              f"mean={sum(durs)/len(durs):.2f}s")
        if gaps:
            print(f"  stall-to-stall:     min={min(gaps):.1f}s max={max(gaps):.1f}s "
                  f"mean={sum(gaps)/len(gaps):.1f}s")
        print("\n  [!] STALLS. If they are ~3.0s and arrive every ~6-9s, this is the known")
        print("      libgphoto2 2.5.34 bug and the venv is on the python-gphoto2 WHEEL.")
        print("      Check the version printed at the top, then:")
        print("        pip install --force-reinstall --no-binary gphoto2 gphoto2")
        print("      (backend/requirements.txt pins this; see CONSTRAINTS.md.)")

    def print_retry_summary():
        n = len(retries)
        if not n:
            print("\n===== SUMMARY =====\n  no captures fired.")
            return
        fails = [r for r in retries if not r[1]]
        bare = [r for r in fails if r[2] == "bare"]
        reinit = [r for r in fails if r[2] == "reinit"]
        dead = [r for r in fails if r[2] == "dead"]
        print("\n===== SUMMARY =====")
        print(f"{n} captures, {len(fails)} first-attempt failures "
              f"({100 * len(fails) / n:.0f}%)")
        if not fails:
            print("\n  No failures. If the camera was in MF and you kept a hand moving in")
            print("  frame for all of them, that CONFIRMS autofocus was the cause.")
            print("  If it was in AF on a static scene, this proves nothing — a static")
            print("  scene gives AF a trivial lock. Move something.")
            return
        print(f"  recovered by BARE retry (no re-init): {len(bare)}/{len(fails)}")
        print(f"  needed the RE-INIT:                   {len(reinit)}/{len(fails)}")
        print(f"  unrecoverable (photo lost):           {len(dead)}/{len(fails)}")
        for label, rows in (("BARE", bare), ("RE-INIT", reinit)):
            if rows:
                c = [r[4] for r in rows]
                print(f"  cost when {label:<7} recovered: mean {sum(c)/len(c):.2f}s")
        ff = [r[3] for r in fails]
        print(f"  the failing capture took: min={min(ff):.2f}s max={max(ff):.2f}s "
              f"mean={sum(ff)/len(ff):.2f}s   (~1s = the AF-lock signature)")
        print("\n  VERDICT:")
        if len(fails) < 3:
            print(f"    Only {len(fails)} failure(s) — too few to conclude anything. One lucky")
            print("    bare retry is not evidence. Re-run with more --shots.")
        elif len(bare) >= len(fails) * 0.8:
            print("    The BARE retry recovers almost everything => the re-init is pure cost.")
            print("    Drop `self.connected = False` from trigger_capture's exception path")
            print("    (backend/camera/device.py): a failed shot then costs the guest ~2.5s")
            print("    instead of ~7.8s.")
        elif not bare:
            print("    The bare retry never recovers. BEWARE THE CONFOUND: the re-init path")
            print("    also inserts ~3.2s before retrying, so it may simply be buying time for")
            print("    a moving subject to settle rather than resetting anything. Sweep")
            print("    --bare-delay-s 1.5 to separate the two before touching the camera package.")
        else:
            print("    Mixed. Sweep --bare-delay-s (0.1/0.3/1.5): if the failures that needed a")
            print("    re-init recover with a longer bare delay, it was only ever buying time.")

    def _sigterm(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _sigterm)

    frames = 0
    try:
        if args.retry_probe:
            retry_probe_loop()
        else:
            frames = health_loop()
    except KeyboardInterrupt:
        print("\ninterrupted.")
    finally:
        if args.retry_probe:
            print_retry_summary()
        else:
            print_health_summary(frames)
        try:
            cam.exit()
            print("camera released cleanly (exit()).")
        except Exception as e:
            print(f"[warn] camera exit failed: {e}")


if __name__ == "__main__":
    main()

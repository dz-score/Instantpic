#!/usr/bin/env python3
"""
trace_wall — find THE WALL in a `gphoto2 --debug` log.

WHY THIS EXISTS (2026-07-13). Our whole model of the M50 says live view stalls
~3.0s after ~6.0s of continuous polling, and we shipped a 4.5s shot spacing to
stay inside that window. But Canon's EOS Utility live-views the same body, over
the same cable, for minutes without stalling. Same hardware, different driver —
so the stall is probably not the camera resting; it is more likely something in
libgphoto2's PTP live-view path (or in how we drive it). Our own numbers already
hint at it: the stall lasts 3.00-3.07s EVERY time, which is the signature of a
fixed software timeout, not of hardware contention, and it ends in an error
([-1]) rather than a late frame.

WHY THE CLI AND NOT THE PROBE. preview_stall_probe.py --trace cannot get at this:
python-gphoto2 is bound to its OWN bundled libgphoto2 (2.5.34), which appears to
be built with debugging compiled out — both log routes deliver zero lines while
the system CLI (2.5.30) logs happily. So we trace through the CLI, which CAN log,
and read its output here.

  cd /tmp && gphoto2 --debug --debug-logfile=/tmp/lv.log --capture-movie=60s
  python3 backend/tools/trace_wall.py /tmp/lv.log

--capture-movie is the right command because it is the only CLI mode that holds
ONE session open and calls capture_preview in a loop — exactly the shape of our
preview worker. (A loop of one-shot `--capture-preview` invocations re-inits every
time and would never accumulate the ~6s of continuous live view the stall needs.)

WHAT IT REPORTS. libgphoto2 at --debug logs around every PTP operation, so it is
never quiet for long. Any silence of ~1s+ means we sat inside ONE call for that
whole time. For each such gap this prints the line BEFORE it (the call we were
stuck in) and the line AFTER (how it ended), then aggregates across all of them.

HOW TO READ THE RESULT:
  ends in a TIMEOUT / "Timeout reading from endpoint" / a retry
      => we are waiting out a fixed timeout for a reply the camera never sent.
         The camera is not resting; WE are. Look for what EOS Utility sends and
         we do not (an EOS keepalive, an event poll). A real fix likely exists,
         and the 4.5s shot-spacing constraint could be lifted.
  ends in PTP_RC_DeviceBusy retries
      => the camera is refusing, but it is still talking. Still our protocol bug:
         find what we left open or unread that makes it busy.
  the silence sits INSIDE one usb_bulk_read, nothing logged either side
      => genuinely blocked on the wire; the camera stopped answering. This is the
         one outcome that supports the hardware story and vindicates the shipped
         4.5s spacing as the only fix available.

CAVEAT, and it matters: this traces libgphoto2 2.5.30 (the CLI's), while the booth
runs 2.5.34 (python-gphoto2's bundled copy). If the CLI does NOT stall at all,
that is not a null result — it is a finding: the stall would then live in 2.5.34
or in how we drive it, not in the camera. Say so; do not assume the trace failed.
"""
import argparse
import collections
import re
import sys

# 0.000265 main                        (2): ALWAYS INCLUDE THE FOLLOWING LINES...
LINE = re.compile(r"^(\d+\.\d+)\s+(\S+)\s+\((\d+)\):\s?(.*)$")


def parse(path):
    """Yield (t, func, msg) in file order.

    A gphoto2 log can hold several appended runs, and each run's clock restarts
    at 0.0 — so a timestamp that goes BACKWARDS means a new run began, and any
    "gap" measured across that seam would be fiction. We split there and report
    per-run.
    """
    runs, cur, last_t = [], [], None
    with open(path, "r", errors="replace") as f:
        for raw in f:
            m = LINE.match(raw.rstrip("\n"))
            if not m:
                # Continuation of a multi-line message (hex dumps, etc.) — it
                # carries no timestamp of its own, so it cannot start or end a
                # silence. Attach it to the previous entry and move on.
                if cur and raw.strip():
                    cur[-1][2] += " " + raw.strip()[:80]
                continue
            t, func, _lvl, msg = float(m.group(1)), m.group(2), m.group(3), m.group(4)
            if last_t is not None and t < last_t - 0.5:
                runs.append(cur)
                cur = []
            cur.append([t, func, msg])
            last_t = t
    if cur:
        runs.append(cur)
    return runs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logfile")
    ap.add_argument("--min-gap", type=float, default=1.0,
                    help="a silence this long (s) counts as a stall (default 1.0)")
    ap.add_argument("--context", type=int, default=3,
                    help="log lines to show either side of each silence")
    args = ap.parse_args()

    runs = parse(args.logfile)
    if not runs:
        sys.exit(f"no gphoto2 --debug lines found in {args.logfile} — "
                 "was it written with --debug --debug-logfile=?")

    # A DATA-level log hexdumps EVERY viewfinder frame (~46KB each) into the log
    # file. On a Pi writing to the SD card that is a huge amount of I/O sitting
    # right inside the polling loop, and it inflates — or outright manufactures —
    # the very silences we are here to measure. Structural facts (which PTP
    # opcodes were sent, whether anything timed out) survive it; TIMING does not.
    total = sum(len(r) for r in runs)
    hexed = sum(1 for r in runs for e in r if "(hexdump of" in e[2])
    if hexed > total * 0.1:
        print("!" * 72)
        print(f"  WARNING: {hexed}/{total} lines are hexdumps — this is a DATA-level log.")
        print("  Every ~46KB frame is being written to disk inside the polling loop, so the")
        print("  TIMING below is distorted and some 'silences' may be log-write artifacts.")
        print("  Trust the structure (opcodes, timeouts, errors); do NOT trust the durations.")
        print("  Re-run with less distortion before drawing timing conclusions:")
        print("    gphoto2 --help | grep -i debug      # is there a --debug-loglevel?")
        print("    # and write the log to RAM, not the SD card:")
        print("    cd /tmp && gphoto2 --debug --debug-logfile=/dev/shm/lv.log \\")
        print("        --capture-movie=60s")
        print("!" * 72)

    walls = []
    for ri, entries in enumerate(runs, 1):
        if len(runs) > 1:
            print(f"\n########## run {ri}/{len(runs)} "
                  f"({len(entries)} lines, {entries[-1][0] - entries[0][0]:.1f}s) ##########")
        for i in range(1, len(entries)):
            gap = entries[i][0] - entries[i - 1][0]
            if gap < args.min_gap:
                continue
            before, after = entries[i - 1], entries[i]
            walls.append((gap, before[1], after[1], before[2], after[2], before[0]))
            print(f"\n--- SILENCE {gap:.2f}s  @ t={before[0]:.2f}s ---")
            for e in entries[max(0, i - 1 - args.context):i - 1]:
                print(f"    {e[0]:8.3f} {e[1]:<28} {e[2][:90]}")
            print(f"  > {before[0]:8.3f} {before[1]:<28} {before[2][:90]}")
            print(f"  {'':8} {'':28} {'':>10}<<< {gap:.2f}s OF SILENCE - stuck in the call above")
            print(f"  > {after[0]:8.3f} {after[1]:<28} {after[2][:90]}")
            for e in entries[i + 1:i + 1 + args.context]:
                print(f"    {e[0]:8.3f} {e[1]:<28} {e[2][:90]}")

    # How much live view actually ran, and how many frames came back? Without this
    # a clean log is ambiguous — "no stalls" means nothing if live view only ran
    # for 3 seconds. Our model predicts a stall every ~9s (~6s healthy + ~3s
    # stall), so a long unbroken stretch is a direct contradiction of it.
    print("\n===== LIVE VIEW =====")
    for ri, entries in enumerate(runs, 1):
        span = entries[-1][0] - entries[0][0]
        frames = sum(1 for e in entries if "EOS_GetViewFinderData" in e[2]
                     and "Sending" in e[2])
        tag = f"run {ri}: " if len(runs) > 1 else ""
        print(f"  {tag}{span:.1f}s, {frames} viewfinder frames requested "
              f"({frames / span if span else 0:.1f}/s)")
        w_in_run = [w for w in walls if entries[0][0] <= w[5] <= entries[-1][0]]
        marks = [entries[0][0]] + sorted(w[5] for w in w_in_run) + [entries[-1][0]]
        healthy = [b - a for a, b in zip(marks, marks[1:])]
        if healthy:
            print(f"  longest unbroken stretch: {max(healthy):.1f}s "
                  f"(model says a stall must land every ~6s)")
            if max(healthy) > 12:
                print(f"  -> {max(healthy):.1f}s of continuous live view with NO stall "
                      f"CONTRADICTS the ~6s model.")
                print("     Either this libgphoto2 (CLI, 2.5.30) does not have the bug the")
                print("     booth's (2.5.34) does, or the ~6s clock is not what we think.")

    print("\n===== THE WALL =====")
    if not walls:
        print(f"  No silence >= {args.min_gap}s anywhere in the log.")
        print("\n  Do NOT read this as 'the trace failed'. If live view ran long enough")
        print("  to have stalled (>= ~10s in one session) and did not, then THIS")
        print("  libgphoto2 does not stall — and since the booth (2.5.34, bundled with")
        print("  python-gphoto2) does, the stall is not the camera. It is the library")
        print("  version or the way we drive it, and a real fix exists.")
        print("  Check the run length above before concluding anything.")
        return

    gaps = [w[0] for w in walls]
    print(f"  {len(walls)} silences >= {args.min_gap}s | "
          f"min={min(gaps):.2f}s max={max(gaps):.2f}s mean={sum(gaps)/len(gaps):.2f}s")
    # A near-constant duration is the tell: hardware contention is not repeatable
    # to the centisecond, a fixed timeout constant is.
    if max(gaps) - min(gaps) < 0.25:
        print(f"  -> duration is CONSTANT to within {max(gaps) - min(gaps):.2f}s. That is a")
        print("     fixed timeout being waited out, not a camera resting.")

    starts = [w[5] for w in walls]
    if len(starts) > 1:
        rhythm = [b - a for a, b in zip(starts, starts[1:])]
        print(f"  stall-to-stall: min={min(rhythm):.2f}s max={max(rhythm):.2f}s "
              f"mean={sum(rhythm)/len(rhythm):.2f}s  (our model says ~9s: ~6s healthy + ~3s stall)")

    stuck = collections.Counter(f"{w[1]}: {w[3][:60]}" for w in walls)
    ended = collections.Counter(f"{w[2]}: {w[4][:60]}" for w in walls)
    print("\n  STUCK IN (the call the silence began after):")
    for line, n in stuck.most_common(5):
        print(f"    {n:>3}x  {line}")
    print("\n  ENDED WITH (how the silence broke):")
    for line, n in ended.most_common(5):
        print(f"    {n:>3}x  {line}")
    print("\n  Read it:")
    print("    timeout / retry      -> WE are waiting out a fixed timeout for a reply that")
    print("                            never came. Find the command EOS Utility sends and we")
    print("                            don't (keepalive / event poll). The 4.5s spacing")
    print("                            constraint could then be LIFTED.")
    print("    PTP_RC_DeviceBusy    -> camera refusing but still talking; our protocol bug.")
    print("    inside a bulk_read   -> genuinely blocked on the wire; the hardware story")
    print("                            holds and 4.5s spacing stays the only fix.")


if __name__ == "__main__":
    main()

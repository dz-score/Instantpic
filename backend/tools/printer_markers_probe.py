#!/usr/bin/env python3
"""
printer_markers_probe — find out what the printer actually says about its media.

WHY THIS EXISTS (2026-08-27). CupsPrinterDriver._read_media() was written
against the CUPS marker convention and Gutenprint's changelog, with no DNP
DS-RX1HS on the bench to check it. Nobody has seen the real output. The parser
is therefore a hypothesis, and shipping a hypothesis that reports a confident
"612 prints left" would be worse than reporting nothing.

This dumps the ground truth beside what the parser made of it, so correcting one
against the other is a five-minute job on the day the printer arrives rather
than an afternoon of poking.

    python3 backend/tools/printer_markers_probe.py DS-RX1

Run it on the Pi, with the printer on, powered, and idle. Then run it again with
a nearly-spent ribbon if you can — the interesting question is not whether the
count parses, it is whether it parses the same way near zero.

WHAT TO DO WITH THE OUTPUT
  - If PARSED matches the RAW markers, delete this file. The question is
    answered and Rule 24 says the scaffolding goes.
  - If it does not, fix CupsPrinterDriver._read_media() against the RAW block
    and record what the printer actually reports in Docs/CONSTRAINTS.md §10.
  - If there are no markers at all, the DNP backend is not reporting them
    through this queue. Say so in the docs and leave prints_remaining as None —
    the UI already treats absent as "cannot know" rather than "empty".
"""

import shutil
import subprocess
import sys
from urllib.parse import quote


def run(argv, timeout=10):
    """Return (ok, text). Never raises — a missing tool is a finding, not a crash."""
    if shutil.which(argv[0]) is None:
        return False, f"{argv[0]} is not installed"
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"{argv[0]} timed out after {timeout}s"
    except OSError as e:
        return False, f"{argv[0]} could not run: {e}"
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0, out.rstrip()


def section(title, body):
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")
    print(body or "(no output)")


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        print("usage: printer_markers_probe.py <cups-queue-name>")
        return 2
    queue = sys.argv[1]

    section("QUEUES CUPS KNOWS ABOUT", run(["lpstat", "-p", "-d"])[1])
    section(f"QUEUE STATE — lpstat -l -p {queue}",
            run(["lpstat", "-l", "-p", queue])[1])

    uri = f"ipp://localhost/printers/{quote(queue)}"
    ok, ipp = run(["ipptool", "-t", uri,
                   "/usr/share/cups/ipptool/get-printer-attributes.test"])
    section(f"ALL IPP ATTRIBUTES — {uri}", ipp)

    if ok:
        markers = [ln for ln in ipp.splitlines() if "marker-" in ln]
        section("RAW — just the marker attributes",
                "\n".join(markers) if markers
                else "No marker-* attributes. This queue does not report media.")

    # What the shipping parser makes of all that. Imported last so a broken
    # import cannot cost us the raw dump above, which is the valuable half.
    try:
        sys.path.insert(0, ".")
        from backend.print_service import CupsPrinterDriver

        driver = CupsPrinterDriver(queue)
        media_type, remaining = driver._read_media()
        status = driver.get_status()
        section("PARSED — what CupsPrinterDriver._read_media() believes", "\n".join([
            f"  media_type       = {media_type!r}",
            f"  prints_remaining = {remaining!r}",
            "",
            f"  full status: {status.to_dict()}",
        ]))
    except Exception as e:
        section("PARSED — failed", f"{type(e).__name__}: {e}")

    print("\nCompare RAW against PARSED. See the header of this file for what to "
          "do with each outcome.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

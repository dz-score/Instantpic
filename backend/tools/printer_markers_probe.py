"""
printer_markers_probe — find out what the printer actually says about its media.

    python3 backend/tools/printer_markers_probe.py DS-RX1

Dumps the queue's raw CUPS marker attributes beside what
CupsPrinterDriver._read_media() makes of them. Run it on the Pi with the printer
on, powered and idle, then compare RAW against PARSED.

The parser was written without a DS-RX1HS on the bench, so it is a hypothesis;
Docs/PRINTER_NOTES.md records exactly what it assumes and what to do with each
outcome here, including deleting this file once the question is answered
(Rule 24).
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

    print("\nCompare RAW against PARSED. Docs/PRINTER_NOTES.md says what to "
          "do with each outcome.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

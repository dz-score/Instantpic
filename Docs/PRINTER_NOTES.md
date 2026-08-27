# PRINTER_NOTES — DNP DS-RX1HS Field Knowledge

What we know about driving a **DNP DS-RX1HS** dye-sublimation printer from this
booth, and — just as important — what we have only *assumed*. Same role for the
printer that [CAMERA_NOTES.md](CAMERA_NOTES.md) plays for the M50.

> **Status: the printer was ordered 2026-08-27 and the software was written
> ahead of it.** Everything below marked ⚠️ is reasoning from documentation, not
> from a printer anyone has plugged in. The hardware run at the bottom is what
> turns those into facts. Delete the ⚠️ markers as they are confirmed, and
> correct what turns out to be wrong.

---

## The one thing to understand: acceptance is not printing

A dye-sub queue accepts a job in about **100 ms**. The printer then takes about
**12 s** to put it on paper. Every failure that actually happens at an event —
ribbon out, paper out, jam, printer switched off — lands **in that gap**.

This is not hypothetical: the booth used to report the submission as the print,
so a guest whose print had jammed was shown "Done!" and walked away with nothing.

Two consequences shape the code around it, both written up as rules in
[CONSTRAINTS](CONSTRAINTS.md) §10:

- The print lane is occupied for the **whole** print, ~12 s rather than ~100 ms,
  which is why printing has a job-queue lane to itself.
- **Retry belongs to submission only.** Past acceptance the booth reports and
  stops; `REPRINT` hands the retry to a human who can see the printer.

---

## Why `await_job` watches the queue and not the job

The obvious approach is to ask CUPS what state the job ended in. It does not
work from the CLI: **`lpstat -W completed` is IPP `which-jobs=completed`, which
returns completed, aborted *and* canceled jobs in one list.** It cannot tell a
good print from a jam.

The queue can. Out of ribbon, out of paper, a jam and a power-off all **stop the
queue and leave the job sitting in it**. So:

| Observation | Meaning |
|---|---|
| job id gone from `lpstat -o <queue>` | completed |
| job still queued **and** queue stopped | failed — carries CUPS's own reason line |
| still queued at the 90 s deadline | timeout |
| 3 consecutive unreadable polls | unknown (reported, never passed off as success) |

Order matters and is tested: the job-left check runs **first**, so a queue
stopped just after our print finished does not fail a print that came out.

**Known imprecision:** a job an operator cancels from the CUPS web UI also leaves
the queue and reads here as completed. That mislabels something they did
deliberately and already know about — a far better trade than missing a jam.

`JOB_TIMEOUT_S` is 90 s: generous against ~12 s on a serial lane, and inside the
LED firmware's ~120 s printing-mode timeout (see [LED_SPEC.md](LED_SPEC.md) §5)
so the ring only falls to Error if the *backend* hung, not merely a slow print.

---

## ⚠️ Media reporting — the parser is a hypothesis

`CupsPrinterDriver._read_media()` was written from the CUPS marker convention
and Gutenprint's changelog. **Nobody has seen a DS-RX1HS answer.**

What it assumes:

- The Gutenprint dyesub backend publishes standard CUPS **marker attributes**
  (`marker-levels`, `marker-message`, `marker-names`, `marker-types`, …).
- **The count is in `marker-message`, not `marker-levels`.** CUPS
  `marker-levels` is a 0–100 percentage by convention (with −1/−2/−3 for
  unknown/unavailable); the dyesub backend puts the native prints-remaining in
  the message. Reading `87` from levels and calling it 87 prints would be
  reporting a percentage as a count.
- The message looks roughly like `612 native prints remaining on 4x6 ribbon`, so
  the first integer is the count and a `NxM` token is the media size.

How it fails: **closed** — unparseable yields `None`, which the UI renders as no
number at all ([CONSTRAINTS](CONSTRAINTS.md) §10 for why that matters).

Markers are read through **cupsd via `ipptool`**, never by invoking the
Gutenprint backend directly: it owns the USB device while CUPS has the printer.
`ipptool` ships in `cups-ipp-utils` and is not always installed; its absence is
latched after one attempt, otherwise every 5 s status poll would spawn a
subprocess that cannot work.

`backend/tools/printer_markers_probe.py` dumps the raw markers beside what the
parser made of them. Run it on hardware day, then:

- **PARSED matches RAW** — delete the probe. The question is answered and Rule 24
  says the scaffolding goes. Drop the ⚠️ from this section and record what the
  printer reports.
- **They disagree** — fix `_read_media()` against the RAW block, and rewrite the
  assumptions above to match.
- **No markers at all** — the backend does not report them through this queue.
  Say so here and leave `prints_remaining` as `None`; the UI already treats
  absent as "cannot know" rather than "empty".

---

## ⚠️ Geometry — the squeeze this is defending against

Gutenprint has a **known "printout gets squeezed" bug on this model**. Two
things are in place against it, both of which need confirming on paper:

1. **The composite carries a real `dpi=(300, 300)` tag**, and
   2. **`printer_options` defaults to `media=w288h432 scaling=100`** rather than
   `fit-to-page`. Both take the scaling decision away from CUPS —
   [CONSTRAINTS](CONSTRAINTS.md) §6 is the rule and the reasoning.

The part that is specific to this printer, and unconfirmed: `w288h432` is 4×6 in
in CUPS media names, but the Gutenprint DS-RX1 PPD may want its own `PageSize`
choice instead. That is bench question one, and why the field is editable from
the Printer tab rather than hardcoded.

The **test print** exists for this. It prints an alignment card, not a photo,
because "did paper come out" is not the setup question. Read it like this:

- The **outer rule sits on the paper edge**. Any of it missing is bleed being lost.
- **Ticks are every half inch**, numbered in inches. Count what survived to
  measure how much was cropped; compare the two axes to catch a squeeze.
- **The circle is a true circle.** An ellipse means the aspect is wrong, which no
  amount of counting ticks makes as obvious.
- **TOP LEFT** in the corner catches a rotated or mirrored page.

---

## ⚠️ What CUPS should be told about this printer

- The RX1HS **enumerates as a DS-RX1** — the HS is a firmware and media
  revision, not a separate model as far as the driver is concerned.
- It needs the **`gutenprint53+usb` CUPS backend**, which exists specifically for
  the DS-RX1/RX1HS USB protocol rather than the generic USB backend.
- Media is a roll: **700 prints at 4×6**, or 350 at 6×8.

Install steps live in [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) §6.

---

## The hardware run

Do these in order the day the printer arrives. Everything before this point is
verified only against the mock.

**1. Get a queue.** Install `printer-driver-gutenprint`, restart CUPS, add the
printer over USB. Confirm it appears as **DS-RX1** with a Gutenprint driver, and
that the booth user can reach the USB device without root.

**2. Prove the geometry.** Admin panel → Printer → **Print Alignment Card**.
Against a ruler:
- [ ] The outer rule is present on all four edges (nothing cropped).
- [ ] The 1″ ticks are 1″ apart, on **both** axes (no squeeze).
- [ ] The circle is round, not oval.
- [ ] TOP LEFT is top left (not rotated, not mirrored).
- [ ] Then a real composite, and check a face is not stretched.

If any of that fails, edit **Print options** on the same tab and print again —
that is what the field is for. Record the string that worked here.

**3. Time it.** Measure a real 4×6 end to end. Feed the number back into
`printer_mock.job_duration_s` so the dev box keeps rehearsing the truth, and
sanity-check it against `JOB_TIMEOUT_S = 90`.

**4. Capture the markers.** `python3 backend/tools/printer_markers_probe.py
DS-RX1`. Compare RAW against PARSED, correct `_read_media()`, and rewrite the
⚠️ section above with what the printer actually says. Run it again on a nearly
spent ribbon if you can — the interesting question is not whether the count
parses, it is whether it parses the same way near zero.

**5. Break it on purpose.** For each of these, the guest screen must say the
print did not come out, keep the QR, and offer a retry — and the LED ring must
leave the printing animation:
- [ ] Open the media door mid-job.
- [ ] Switch the printer off mid-job.
- [ ] Unplug the USB cable mid-job.
- [ ] Run the ribbon out completely.
- [ ] Then fix it and tap **Try Printing Again** — the same photo must print.

**6. Check the honest cases.** With the printer working:
- [ ] `printStatus` stays `printing` for the *whole* physical print, not ~100 ms.
- [ ] "Your Print is Ready!" appears when the paper is actually out.
- [ ] Prints remaining on the Printer tab counts down by one per print.
- [ ] Set the low threshold above the current count and confirm the warning
      appears on the tab, in the System card, and **once** in the log.

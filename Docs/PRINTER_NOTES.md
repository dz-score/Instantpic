# PRINTER_NOTES — DNP DS-RX1HS Field Knowledge

What we know about driving a **DNP DS-RX1HS** dye-sublimation printer from this
booth, and — just as important — what we have only *assumed*. Same role for the
printer that [CAMERA_NOTES.md](CAMERA_NOTES.md) plays for the M50.

> **Status: printer on the bench since 2026-08-29.** The software was written
> ahead of it, so sections still marked ⚠️ are reasoning from documentation that
> nothing has yet checked against the hardware. Unmarked sections have been.
> Drop the ⚠️ as each is confirmed, and correct what turns out to be wrong.

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

The queue can. Out of ribbon, out of paper and a jam all **stop the queue and
leave the job sitting in it**. So:

| Observation | Meaning |
|---|---|
| job id gone from `lpstat -o <queue>` | completed |
| job still queued **and** queue stopped | failed — carries CUPS's own reason line |
| job still queued **and** the job alerts `resources-are-not-ready` (3 polls) | failed — printer absent or no media |
| still queued at the 90 s deadline | timeout |
| 3 consecutive unreadable polls | unknown (reported, never passed off as success) |

**A switched-off printer does NOT stop the queue** — this was assumed for a
long time and is wrong. Measured on the booth's own DS-RX1 with the printer
powered down, `lpstat -p` reports the queue perfectly healthy while the job
alone carries the fault:

```
$ lpstat -p DS-RX1
printer DS-RX1 is idle.  enabled since Mon 31 Aug 2026 12:35:06 AM CEST
$ lpstat -l -o DS-RX1
DS-RX1-18    instantpic  1024  Mon 31 Aug 2026 12:34:56 AM CEST
        Status: Printer open failure (No matching printers found!)
        Alerts: resources-are-not-ready
```

So the queue-stopped check never fired and the guest watched the printing
animation out to the full 90 s before being told anything. `await_job` now also
reads the job's own alerts, which is why the third row above exists. Three
consecutive observations, because a job can report not-ready for an instant at
submission while CUPS opens the device.

### Asking *before* submitting: `lpinfo -v`, not `lpstat`

No `lpstat` form can answer "is the printer plugged in" — `lpstat -p` and
`lpstat -l -p` were measured byte-identical with the DS-RX1 powered on and
powered off. **`lpinfo -v` is different: it probes the backends instead of
reading the queue's cached state, so the USB URI simply stops being listed.**

```bash
lpstat -v DS-RX1     # device for DS-RX1: gutenprint53+usb://dnp-dsrx1/CB2D63217299
lpinfo -v            # that URI is listed only while the printer is powered on
```

`device_present()` matches the two and is used in two places: to refuse a
submission that would only queue work nobody can print, and to stop the admin
panel reporting an unplugged printer as "Idle" — which it did, because `lpstat`
describes the queue and the queue is fine.

Probe with `--include-schemes <scheme>`; a bare `lpinfo -v` also walks the
network backends and costs seconds of discovery on a venue LAN.

**It returns `None` for "could not tell", and neither caller treats that as a
missing printer.** `lpinfo` may want privileges the booth does not have, and a
diagnostic that cannot run must never ground a printer that is sitting there
working. The in-flight alert check above remains the backstop regardless: the
printer can be switched off in the seconds after any pre-check passes.

Order matters and is tested: the job-left check runs **first**, so a queue
stopped just after our print finished does not fail a print that came out.

**Known imprecision:** a job an operator cancels from the CUPS web UI also leaves
the queue and reads here as completed. That mislabels something they did
deliberately and already know about — a far better trade than missing a jam.

`JOB_TIMEOUT_S` is 90 s: generous against ~12 s on a serial lane, and inside the
LED firmware's ~120 s printing-mode timeout (see [LED_SPEC.md](LED_SPEC.md) §5)
so the ring only falls to Error if the *backend* hung, not merely a slow print.

---

## The queue latches disabled — set the error policy (2026-08-29)

**Measured on the booth.** The cover was opened during a print. What followed:

| | |
|---|---|
| 14:46, 14:53 | two jobs print normally |
| 15:00:13 | job 3 submitted |
| **15:00:15** | **CUPS disables the queue — "Cover Open"** |
| 15:00:32 → 15:07:22 | jobs 4-7 accepted into a dead queue, none print |

Closing the cover did not help. Power-cycling the printer did not help. Five
jobs sat in the queue and every session reported a fault that had stopped
happening minutes earlier — `lpstat -p` repeats the reason the queue was
stopped, it does not re-read the printer.

**Cause: CUPS defaults to `ErrorPolicy stop-printer`.** Any backend error
disables the queue until a human runs `cupsenable`. Correct for an office
printer somebody walks over to; ruinous for an unattended booth, where one
cover-open at 8pm means nobody gets a print for the rest of the night.

**The queue must be set to `abort-job`:**

```bash
lpadmin -p DS-RX1 -o printer-error-policy=abort-job
```

`abort-job` drops the failed job and leaves the queue running, so the next guest
prints. Not `retry-current-job`: that reprints the previous guest's photo when
the fault clears, to a guest who has left.

The booth checks this at every boot (`printer_preflight` in the log) because a
setting applied by hand is a setting that is eventually not applied — a rebuilt
SD card, a re-added queue, a different printer.

It also **recovers on its own**, before every print: a stopped queue is
re-enabled and whatever was stranded in it is dropped first, logged as
`printer_queue_recovered`. That is belt and braces to the policy above, and the
log line is the signal that matters — a booth silently recovering the same fault
all evening is a printer that needs a human.

**Recovery, if a queue is already stuck.** Clear the backlog *before*
re-enabling, or every stacked job prints at once:

```bash
lpstat -p DS-RX1 -l      # "disabled since ..." and why
lpstat -o DS-RX1         # what is stacked up
cancel -a DS-RX1         # drop the backlog FIRST
cupsenable DS-RX1        # then re-enable
```

---

## Media reporting — what the printer actually says (2026-08-29)

Verbatim, on 6x4 media with a nearly-full roll:

```
marker-levels    (integer)            = 98
marker-low-levels  (integer)          = 10
marker-high-levels (integer)          = 100
marker-message   (textWithoutLanguage) = 692 native prints remaining on 6x4 (PC) media
marker-names     (nameWithoutLanguage) = 6x4 (PC)
marker-types     (keyword)            = ribbonWax
marker-colors    (nameWithoutLanguage) = #00FFFF#FF00FF#FFFF00
```

**The count is in `marker-message`, not `marker-levels`.** Levels is a 0-100
percentage — 98 on a roll with 692 prints left. Reading the count from there
would have shown "98 prints left" on a full roll, which is why
`_read_media()` parses the message and takes the first integer.

Two things that follow from how this is produced:

- **The values are a snapshot, not a live read.** The Gutenprint backend only
  talks to the printer while a job runs, so what CUPS holds is from the last
  job. A queue that has not printed since cupsd started may report nothing at
  all, and the booth will show no number rather than a wrong one.
- `printer-commands = ReportLevels` — CUPS can refresh the markers on demand by
  running the backend without printing. The booth does not use it; if a stale
  count ever becomes annoying between sessions, that is the lever.

To re-check after a media change, or if the count ever looks wrong:

```bash
ipptool -tv ipp://localhost/printers/DS-RX1 \
  /usr/share/cups/ipptool/get-printer-attributes.test | grep marker-
```

**`-tv`, not `-t`.** Without `-v` ipptool prints a PASS/FAIL summary and no
attributes at all — which for a while is exactly what the booth was doing, so
media silently read as unknown no matter what the printer said.

---

## Geometry — the squeeze this was defending against

Gutenprint has a **known "printout gets squeezed" bug on this model**. Two
things are in place against it, and **both were confirmed correct on paper
(2026-08-29)** — the alignment card printed to size on the first attempt:

1. **The composite carries a real `dpi=(300, 300)` tag**, and
   2. **`printer_options` defaults to `media=w288h432 scaling=100`** rather than
   `fit-to-page`. Both take the scaling decision away from CUPS —
   [CONSTRAINTS](CONSTRAINTS.md) §6 is the rule and the reasoning.

`w288h432` — 4×6 in in CUPS media names — is what the Gutenprint DS-RX1 PPD
wants; it needed no adjustment. The field stays editable from the Printer tab
anyway, for a different printer or a different media size.

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

- The RX1HS **enumerates as a DS-RX1** (confirmed 2026-08-29) — the HS is a
  firmware and media revision, not a separate model to the driver.
- It needs the **`gutenprint53+usb` CUPS backend**, which exists specifically for
  the DS-RX1/RX1HS USB protocol rather than the generic USB backend.
- Media is a roll: **700 prints at 4×6**, or 350 at 6×8.

Install steps live in [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) §6.

---

## The hardware run

Do these in order the day the printer arrives. Everything before this point is
verified only against the mock.

Back up `config.json` first — steps 2 and 7 change settings, and one of them is
the thing you will want to put back.

Two of these are likely to fail on the first attempt and both are expected:
the geometry in step 2, because the right `lp -o` string for this PPD is a
guess, and the marker parse in step 4, because nobody has seen the output. Both
are fixable on the spot. Everything else should already work.

**1. Get a queue.** Install `printer-driver-gutenprint`, restart CUPS, add the
printer over USB. Confirm it appears as **DS-RX1** with a Gutenprint driver, and
that the booth user can reach the USB device without root. Then set
`printer-error-policy=abort-job` — the booth logs `printer_preflight` at boot if
you forget, and the section above is what happens when nobody does.

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

**4. Check the media count.** Done for 6x4 (see above) — the Printer tab should
show prints remaining within five seconds of opening it. Worth redoing on a
nearly spent ribbon: the count parses on a full roll, and whether the message
keeps that shape near zero is untested.

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

**7. The three things only real timing can test.** All of these are correct
against the mock and have never met a printer that takes twelve seconds:

- [ ] **Clear the queue mid-print.** Admin → System → *Clear Print Queue* while
      a print is running. The guest must be told the print did not come out.
      Reporting "ready" here is the exact bug `cancel_all`'s epoch exists to
      stop, and the operator pressing that button has usually just seen a jam.
- [ ] **Stop the service mid-print.** `sudo systemctl stop photo-booth` with a
      print in flight. It must stop in a second or two. If it hangs for ~90 s
      the abort event is not reaching the driver, and systemd will eventually
      SIGKILL it instead.
- [ ] **Spend the allowance.** Set *Prints allowed* to one above the current
      count, run two sessions. The first prints; the second reaches the same
      screen but says prints have run out, still shows the QR, and offers no
      retry. Then **Reset count** and confirm printing resumes.
- [ ] Restart the booth mid-event and confirm `prints_used` survived — a budget
      that resets on reboot is not a budget.

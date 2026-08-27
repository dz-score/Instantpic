# host_test — led-node unit tests, no hardware

```
make          # build and run everything
make compile  # build only
```

Needs a C compiler and nothing else. No ESP-IDF, no board, no strip.

**Status: 40 assertions, all passing** (parse 11, canvas 11, modes 18), on
gcc 16.1 / MSYS2 UCRT64.

> **On Windows with MSYS2**, use the UCRT64 toolchain and **`mingw32-make`**:
>
> ```
> export PATH="/d/softwares/msys64/ucrt64/bin:$PATH"
> mingw32-make check
> ```
>
> MSYS2's `/usr/bin/make` fails here with `Cannot create temporary file in
> C:\WINDOWS\: Permission denied` — it is `make` itself, not the compiler, and
> `mingw32-make` from `ucrt64/bin` does not have the problem. The compiler is
> fine either way; `cc` and `gcc` both work.

## What is covered

| Suite | Under test | Why it is worth testing |
|---|---|---|
| `test_parse` | `command_parse()` | The one component both transports share, so a divergence here is a divergence everywhere |
| `test_modes` | `apply()`, `check_deadlines()` | Every mode transition and all four timeouts |
| `test_canvas` | Canvas maths | Sub-pixel rendering and linear-space blending |

`test_modes` opens with the regression for `14a6f00`: before that fix, a `PING`
arriving in Link Lost returned `PONG` and left the mode unchanged, so a
recovered host saw a healthy link while the ring kept showing the fault pattern.
It is a four-line test, and it would have caught the bug the day it was written.

### Two things the first run taught us

Both were wrong assumptions in the tests, not firmware faults — which is what a
first run is for.

**Pixel `i` is centred at `(i + 0.5) * DEG_PER_PIXEL`.** Pixel 0 sits at 3°, not
0°, so 0° is a *boundary* and 3° is a *centre*. The sub-pixel tests originally
had these exactly backwards and asserted the opposite of what they set up.

**The link watchdog is shorter than the capture and printing timeouts.** At 10 s
versus 30 s and 120 s, advancing a fake clock with nothing arriving reaches Link
Lost long before either timeout — so those tests need a live link
(`advance_alive_ms`, which feeds `last_rx_ms` every 2 s the way the Pi's
heartbeat does). This is real behaviour worth knowing: with a *dead* host the
ring leaves full white after 10 s, not 30. Both orderings are now asserted.

## How it works

Three things make the firmware testable off-target, and all three were design
decisions rather than accidents:

- **The parser is pure** and depends on nothing but a `QueueHandle_t` typedef.
- **Animations are pure functions of elapsed time**, so a mode can be evaluated
  at an arbitrary `t` without having been "running".
- **Every deadline derives from `esp_timer_get_time()`**, so a settable fake
  clock turns "wait 120 seconds for the printing timeout" into an assignment.
  The 30 s capture release and the 10 s link watchdog become instant and
  deterministic.

`stubs/` holds the smallest ESP-IDF surface the code under test actually names —
FreeRTOS types, no-op logging, a controllable clock. It is not a simulator and
should not grow into one. If a test needs a real scheduler, it belongs on the
bench in [`../../Docs/LED_NODE_TESTING.md`](../../Docs/LED_NODE_TESTING.md), not
here.

`test_modes.c` `#include`s `modes.c` rather than linking it, because `apply()`
and `check_deadlines()` have internal linkage. That is the standard way to reach
statics in C, and it is why the mode logic is testable without inventing an
accessor that production has no use for.

## What this cannot tell you

Anything involving real time, real hardware, or photons: RMT timing, the WiFi
stack's effect on frame cadence, PWM banding under a 1/200 s shutter, colour
rendition, current draw, thermal shift. Those are the bench procedure's job, and
several of them need a strip attached — which has still never happened.

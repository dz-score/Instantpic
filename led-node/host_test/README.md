# host_test — led-node unit tests, no hardware

```
make          # build and run everything
make compile  # build only
```

Needs a C compiler and nothing else. No ESP-IDF, no board, no strip.

> **Status: compiled, never executed.** These were written on a Windows machine
> with no host C compiler and no emulator. Every file compiles warning-free and
> links with all non-libc symbols resolved (verified with the `riscv32-esp-elf`
> cross-compiler), but **no assertion in here has ever actually run**. Run
> `make` on the Pi or any Linux box before trusting a green result — until then
> a passing assertion and a wrong assertion look identical.

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

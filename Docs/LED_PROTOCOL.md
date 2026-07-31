---
status: current
last-reviewed: 2026-07-31
applies-to-commit: 2ebb471
---

# LED_PROTOCOL — Wire Reference

The single source of truth for the command vocabulary spoken to the LED node.
ASCII, newline-terminated, human-typeable on purpose — the whole booth can be
driven from a serial monitor or a browser bar with no tooling.

The same lines and the same parser
([`command_parse()`](../led-node/components/protocol/parse.c)) serve every
transport. Nothing below the transport layer can tell which one is running.

- What the ring *does* with these: [LED_SPEC.md](LED_SPEC.md)
- How the firmware is arranged: [LED_NODE_ARCHITECTURE.md](LED_NODE_ARCHITECTURE.md)

---

## Commands

| Line | Argument | Success reply | Result |
|---|---|---|---|
| `IDLE` | — | `OK IDLE` | → Idle |
| `PHASE <hue>` | 0–359 | `OK PHASE` | → Playful, hue stored |
| `COUNTDOWN <ms>` | 1–60000 | `OK COUNTDOWN` | → Countdown; node runs its own clock |
| `CAPTURE` | — | `OK CAPTURE` | → Capture. **Wait for this reply, then fire the shutter.** |
| `RELEASE` | — | `OK RELEASE` | → Idle. Exact alias of `IDLE` — same `case` in `apply()` |
| `PRINTING` | — | `OK PRINTING` | → Printing |
| `FINISHED <ms>` | 1–60000 | `OK FINISHED` | → Finished; returns to Idle on its own |
| `ERROR <code>` | ≥ 0, effective 1–9 | `OK ERROR` | → Error; code = heartbeat groups |
| `PING` | — | `PONG` | No mode change, **except** from Boot or Link Lost → Idle |

**`ERROR <code>` clamps rather than rejects.** Any non-negative integer parses.
`anim_error` renders `code` as the number of double-pulse groups, treating 0 as
1 and clamping above 9. A `PRINTING` timeout enters Error with code 0
internally, so it shows as one group.

**Re-sending a command restarts it.** `enter()` unconditionally resets the mode
entry time, so a second `COUNTDOWN 3000` restarts the clock rather than being
ignored.

**Every command is legal in every mode.** `apply()` is a flat switch with no
gating — a `COUNTDOWN` during `PRINTING` is accepted. The node is a pure sink;
the Pi owns sequencing.

## Replies

Exactly one line per command. Success is `OK <VERB>` — or `PONG` for `PING`,
which is the one asymmetry, kept because a heartbeat that echoed `OK PING`
would read as a mode acknowledgement.

### Errors

Seven, from three different layers. **All of them are normal replies, not
transport failures.**

| Reply | Layer | Cause |
|---|---|---|
| `ERR UNKNOWN` | `transport_common.c` | Unknown verb, missing or non-numeric argument, negative number, or extra tokens after a no-argument verb |
| `ERR RANGE` | `modes.c` `apply()` | Verb understood, argument outside the table above. **Mode unchanged.** |
| `ERR TOOLONG` | both transports | Line ≥ `CMD_LINE_MAX` (64 bytes) |
| `ERR BUSY` | `transport_common.c` | Command queue full (depth 8) after waiting 200 ms to enqueue |
| `ERR TIMEOUT` | `transport_common.c` | Render task did not answer within `TRANSPORT_REPLY_TIMEOUT_MS` (200 ms) |
| `ERR NOCMD` | HTTP only | `GET /cmd` with no `c=` parameter |
| `ERR RECV` | HTTP only | `POST /cmd` body could not be read |

`ERR UNKNOWN` is never a fault state — the firmware and the backend version
independently, and drift has to degrade gracefully.

> ### `ERR TIMEOUT` is ambiguous, and the ambiguity is load-bearing
>
> It means **the command may or may not have been applied.** The transport gave
> up waiting; the render task may still have picked the command off the queue
> and acted on it a moment later.
>
> For `CAPTURE` this is the dangerous case. The ring may be at full white with
> no acknowledgement, or not lit at all, and the reply does not distinguish
> them. A client that treats it as "failed" may fire into a dark ring; one that
> treats it as "succeeded" may fire into a ring that never lit.
>
> Whatever the Pi does here is a real design decision. `GET /state` resolves it
> definitively — it reports the current mode — at the cost of another
> round trip on a link that has just demonstrated it is slow.

## Line format

- **Case-insensitive** verbs (`strncasecmp`).
- **Leading and trailing whitespace trimmed**, including `\t`, `\r` and `\n`, so
  a line typed from a Windows serial monitor works.
- **Verb and argument split at the first whitespace run**; multiple spaces
  between them are fine.
- **Arguments are decimal digits only.** No sign, no whitespace inside, no hex.
  A leading `-` makes the line `ERR UNKNOWN`, not `ERR RANGE`. Values above
  `INT32_MAX` are rejected as malformed.
- **Extra tokens after a no-argument verb are rejected** rather than ignored —
  a caller sending them thinks it is speaking a protocol we do not have.
- **`CMD_LINE_MAX` is 64 bytes**, `CMD_REPLY_MAX` is 48.

## Heartbeat

`PING` every **~2 s**. It is not debug sugar.

The link watchdog measures time since **any** inbound line, not since the last
mode change (`MODE_LINK_TIMEOUT_MS`, 10 s). A booth sitting in Idle for an hour
would otherwise trip into Link Lost while nothing is wrong.

Entry and recovery are symmetric and both are the heartbeat's job: 10 s of
silence enters Link Lost, and the next `PING` leaves it. Boot behaves the same
way, so a node powered on while the Pi is already up reaches Idle without a mode
command.

**A client that resumes pinging after a reconnect is therefore sufficient** to
restore the ring — as of `14a6f00`. It does not need to re-send a mode command.

Because the watchdog counts *any* line, a heartbeat is only needed when the wire
has been idle. Pinging on a fixed 2 s timer is correct but wasteful; pinging
only when nothing else has been sent for ~2 s keeps a heartbeat from queueing
ahead of `CAPTURE` during the countdown window.

## Transport framing

The line is identical. Only the envelope differs.

### HTTP

```
POST /cmd            body: COUNTDOWN 3000
GET  /cmd?c=CAPTURE
```

- **`ERR` replies are HTTP 200.** The request was well-formed; the command was
  not. **Never infer command success from the status code — parse the body.**
- Response is `text/plain`, the bare reply line.
- `GET` percent-decodes and treats `+` as a space, so `PHASE%20280` and
  `PHASE+280` both work from a browser bar.
- `POST` bodies of 64 bytes or more get `ERR TOOLONG` before any parsing.

Read-only endpoints, no commands: `GET /` (preview page), `GET /frame` (the
exact bytes the strip received), `GET /state` (render-task introspection).

> **Known limitation.** The `POST` handler does not loop on `httpd_req_recv()`.
> A partial read is treated as a complete body, which would submit a truncated
> line. For bodies this short on a local link it is vanishingly unlikely, but it
> is a real edge under a congested one.

### UART

Lines in, replies out, `\n`-terminated both ways. An overlong line is discarded
through to the next newline and answered with `ERR TOOLONG`, rather than
parsing a truncated command.

> Never compiled or run — see [LED_UART_SWITCH.md](LED_UART_SWITCH.md).

## One command in flight

**The client must not have two commands outstanding at once**, on any transport.

`transport_common.c` uses a single reply queue of depth 1, so command/reply
correlation is **positional**, not keyed. HTTP hides this — `esp_http_server`
serializes handlers on one task, so overlapping requests still get the right
answers — but the constraint is real and UART cannot hide it.

See [LED_UART_SWITCH.md](LED_UART_SWITCH.md) for why this has to be structural
in the Pi client rather than a rule someone remembers.

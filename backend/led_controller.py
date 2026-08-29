"""LED ring controller — booth semantics on one side, command lines on the other.

Owns the heartbeat, the latency instrumentation, and degradation when the node
is absent. The state machine calls the semantic methods here and never sees a
command line; the transport below carries lines and never sees booth state.

### One command in flight, structurally

The protocol allows one command in flight (Docs/LED_PROTOCOL.md). Rather than
leave that as a rule to remember, it is a property of the shape: a single owner
task drains a queue and is the only thing that ever touches the transport, and
callers await a future. Keep it that way — it is also what makes a UART swap a
port rather than a redesign.

### Degradation

A missing or unreachable node must never stop the booth taking photos. Every
method is a no-op when disabled, and a link failure is logged, not raised.

One caveat worth knowing: at CAPTURE the ring is the **key light**, not
decoration. A dead node there means underexposed photos rather than merely
undecorated ones, so `capture()` reports whether it was acknowledged and the
caller decides. That decision is workflow, and workflow belongs to the FSM
(Rule 7).
"""

import asyncio
import time
from collections import deque
from typing import Callable, Optional

from backend.led_transport import LedHttpTransport, LedLinkError, LedTransport
from backend.logger import log

# Commands whose reply is `PONG` rather than `OK <VERB>`.
_PONG_VERBS = {"PING"}

# How many recent round-trips to keep per command class for percentiles. Small:
# this is a health signal, not a time series.
_LATENCY_WINDOW = 256


class _Request:
    __slots__ = ("line", "timeout_s", "future")

    def __init__(self, line: str, timeout_s: float,
                 future: Optional[asyncio.Future]):
        self.line = line
        self.timeout_s = timeout_s
        # None for fire-and-forget. The owner task still serializes the send;
        # the caller simply does not wait for the reply. Only CAPTURE has a
        # reason to wait, and every other command awaiting a round trip would
        # tax every FSM transition with one.
        self.future = future


class LedController:
    """Booth semantics for the LED node. Constructed by the composition root."""

    def __init__(self, transport: Optional[LedTransport], *, enabled: bool,
                 heartbeat_s: float, capture_timeout_s: float,
                 default_timeout_s: float, config_key: Optional[tuple] = None,
                 fault_source: Optional[Callable[[], Optional[int]]] = None):
        self._transport = transport
        self._enabled = enabled and transport is not None
        self._heartbeat_s = heartbeat_s
        self._capture_timeout_s = capture_timeout_s
        self._default_timeout_s = default_timeout_s

        self._queue: Optional[asyncio.Queue] = None
        self._worker: Optional[asyncio.Task] = None
        self._shutdown = False
        # What reconfigure() compares against to decide whether a config save
        # touched the ring at all.
        self._config_key = config_key

        # Instrumentation. This is the evidence the HTTP-vs-UART decision is
        # supposed to rest on (Docs/LED_UART_SWITCH.md), so it is a permanent
        # signal rather than temporary diagnostics (Rule 24).
        self._latency_ms: dict[str, deque] = {}
        self._counts: dict[str, int] = {}
        # Liveness, derived from traffic that was going to happen anyway. The
        # heartbeat already pings whenever the wire is idle, so there is no need
        # for a second prober — and putting extra load on the link under
        # evaluation would corrupt the numbers the evaluation depends on.
        self._last_ok: Optional[float] = None
        self._last_error: Optional[str] = None

        # Fault latch. `fault_source` is polled by the owner task and returns an
        # ERROR code, or None when the booth is healthy. Injected rather than
        # imported: the camera must not learn that a ring exists (Rule 18), so
        # the composition root does the introducing.
        self._fault_source = fault_source
        self._fault: Optional[int] = None
        # The screen command that would be showing if nothing were wrong. Held
        # so the ring can be put back where the booth actually is when the fault
        # clears, rather than waiting for the next transition to repaint it.
        self._screen_line: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if not self._enabled:
            log.info("led", "led_disabled", "LED node disabled; commands are no-ops")
            return

        await self._transport.start()
        self._queue = asyncio.Queue()
        self._worker = asyncio.create_task(self._run(), name="led-worker")
        log.info("led", "led_start",
                 f"LED controller started against {self._transport.description}")

    async def drain(self, timeout_s: float = 2.0) -> bool:
        """Wait until every queued command has reached the node.

        Most callers do not wait for replies, so without this there is no point
        at which a queued line is known to have been sent. Returns False if the
        queue did not clear in time, which means the link is struggling.
        """
        if self._queue is None:
            return True
        try:
            await asyncio.wait_for(self._queue.join(), timeout=timeout_s)
            return True
        except asyncio.TimeoutError:
            return False

    async def stop(self) -> None:
        # Drain first: commands are fire-and-forget, so cancelling the worker
        # outright would silently drop whatever is still queued — including the
        # IDLE that leaves the ring in a sane state at shutdown.
        if self._queue is not None and not self._shutdown:
            await self.drain(timeout_s=1.0)

        self._shutdown = True
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None
        if self._transport is not None:
            await self._transport.stop()
        # Dropped rather than reused: start() makes a fresh one, and leaving the
        # old queue reachable after a reconfigure that disabled the ring would
        # let drain() report on a queue nothing serves.
        self._queue = None

    async def reconfigure(self, settings) -> bool:
        """Apply a changed `led` config block without replacing this object.

        Identity matters: the FSM is handed the controller once at the
        composition root and holds that reference for the process lifetime
        (state_machine.py), so building a second controller would leave the
        booth driving a stopped one. The transport underneath is swapped
        instead.

        Returns True if anything was actually reconfigured. A config save that
        did not touch the ring must not bounce a working link — most admin
        saves are about the couple's names, and the venue is the worst place to
        drop a connection for no reason.

        Counters and latency samples deliberately survive: a link reconfigured
        because it was misbehaving is exactly the one whose history matters
        (Docs/LED_UART_SWITCH.md).
        """
        cfg = settings.led
        desired = _config_key(cfg)
        if desired == self._config_key:
            return False

        await self.stop()

        self._config_key = desired
        self._transport = _build_transport(cfg)
        self._enabled = cfg.enabled and self._transport is not None
        self._heartbeat_s = cfg.heartbeat_ms / 1000.0
        self._capture_timeout_s = cfg.http.capture_timeout_ms / 1000.0
        self._default_timeout_s = cfg.http.timeout_ms / 1000.0
        # stop() latched this to refuse further work; the owner task started
        # below would exit on its first loop otherwise.
        self._shutdown = False

        await self.start()
        return True

    async def set_fault(self, code: Optional[int]) -> None:
        """Latch or clear a booth fault. Idempotent — only edges do anything.

        A fault outranks the screen: while one is latched, screen commands are
        recorded but not sent, because the next FSM transition would otherwise
        paint straight over the error pattern and the operator would never see
        it. Clearing puts the ring back where the booth actually is rather than
        waiting for a transition that may be minutes away.

        Capture and Release are deliberately not suppressed. They bracket the
        shutter rather than a screen, and if a photo is somehow still being
        taken it needs its key light more than the operator needs the red.
        """
        if code == self._fault:
            return

        self._fault = code
        if code is not None:
            log.warn("led", "led_fault", f"Ring showing fault code {code}")
            await self._submit(f"ERROR {max(1, int(code))}")
        else:
            log.info("led", "led_fault_clear", "Ring fault cleared")
            await self._submit(self._screen_line or "IDLE")

    async def _poll_fault(self) -> None:
        if self._fault_source is None:
            return
        try:
            code = self._fault_source()
        except Exception as e:
            # A health probe that throws must not take the ring's owner task
            # down with it — that would strand the heartbeat and the node would
            # drop to Link Lost at a booth that is merely confused.
            log.warn("led", "led_fault_source_error", f"fault source raised: {e}")
            return
        await self.set_fault(code)

    # --- the single owner --------------------------------------------------

    async def _run(self) -> None:
        """Drain the queue; ping when the wire has been idle.

        The heartbeat is folded into the idle timeout rather than run on its
        own timer: a busy session needs no heartbeat at all (Docs/LED_PROTOCOL.md),
        and a separate timer could queue a PING ahead of CAPTURE in the countdown
        window, which is the one place latency is visible.
        """
        while not self._shutdown:
            # Before the wait, not inside _heartbeat(): that only fires when the
            # wire has been idle, so a busy session would never check at all.
            # This way the check happens at least every heartbeat interval, and
            # again whenever a command arrives.
            await self._poll_fault()

            try:
                req = await asyncio.wait_for(self._queue.get(),
                                             timeout=self._heartbeat_s)
            except asyncio.TimeoutError:
                await self._heartbeat()
                continue
            except asyncio.CancelledError:
                raise

            try:
                reply = await self._exchange(req.line, req.timeout_s)
                if req.future is not None and not req.future.done():
                    req.future.set_result(reply)
            except asyncio.CancelledError:
                if req.future is not None and not req.future.done():
                    req.future.cancel()
                raise
            except LedLinkError as e:
                if req.future is not None and not req.future.done():
                    req.future.set_exception(e)
                # Fire-and-forget senders have nowhere to receive this. It is
                # already logged and counted in _exchange, and a dead ring must
                # never surface as an exception inside the booth's workflow.
            finally:
                self._queue.task_done()

    async def _heartbeat(self) -> None:
        try:
            await self._exchange("PING", self._default_timeout_s)
        except LedLinkError:
            # Already logged and counted in _exchange. A missed heartbeat is not
            # itself an event: the node tolerates 10 s of silence, and a link
            # that stays down is reported by the link_down counter.
            pass

    async def _exchange(self, line: str, timeout_s: float) -> str:
        """The only place the transport is touched. Times it, logs anomalies."""
        verb = line.split(" ", 1)[0].upper()
        started = time.perf_counter()
        try:
            reply = await self._transport.send(line, timeout_s)
        except LedLinkError as e:
            self._bump(f"link_error:{verb}")
            self._last_error = f"{verb}: {e}"
            log.warn("led", "led_link_error", f"{verb} failed: {e}")
            # A byte-stream transport can be left mid-line by a timeout; clear it
            # before the next command rather than letting one failure desync
            # every reply after it. No-op over HTTP.
            await self._transport.resync()
            raise

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self._record(verb, elapsed_ms)
        self._last_ok = time.monotonic()
        self._last_error = None

        if not self._is_ack(verb, reply):
            self._bump(f"rejected:{verb}")
            log.warn("led", "led_rejected", f"{verb} -> {reply}")

        return reply

    @staticmethod
    def _is_ack(verb: str, reply: str) -> bool:
        expected = "PONG" if verb in _PONG_VERBS else f"OK {verb}"
        return reply.strip().upper() == expected

    # --- instrumentation ---------------------------------------------------

    def _record(self, verb: str, elapsed_ms: float) -> None:
        self._latency_ms.setdefault(verb, deque(maxlen=_LATENCY_WINDOW)).append(elapsed_ms)
        self._bump(f"sent:{verb}")

    def _bump(self, key: str) -> None:
        self._counts[key] = self._counts.get(key, 0) + 1

    def stats(self) -> dict:
        """Health snapshot. CAPTURE latency is the number that matters.

        Percentiles, not averages: the decision this feeds is about the tail.
        A mean hides exactly the retry storm that would put a dark frame in a
        photo, which is the whole failure mode HTTP is on probation for.
        """
        out: dict = {"enabled": self._enabled, "counts": dict(self._counts)}
        for verb, samples in self._latency_ms.items():
            if not samples:
                continue
            ordered = sorted(samples)
            out.setdefault("latency_ms", {})[verb] = {
                "n": len(ordered),
                "p50": round(_percentile(ordered, 0.50), 1),
                "p95": round(_percentile(ordered, 0.95), 1),
                "p99": round(_percentile(ordered, 0.99), 1),
                "max": round(ordered[-1], 1),
            }
        return out

    def health(self) -> dict:
        """What the admin panel shows, and what /api/diagnostics reports.

        `connected` is inferred rather than probed: the heartbeat guarantees an
        exchange at least every heartbeat_ms whenever nothing else is talking,
        so "the last one succeeded recently" is the same evidence a probe would
        gather, at no cost to the link. The allowance is 3x the interval, which
        tolerates one lost heartbeat without flapping the indicator.
        """
        now = time.monotonic()
        age = None if self._last_ok is None else round(now - self._last_ok, 1)
        connected = (self._enabled and age is not None
                     and age <= self._heartbeat_s * 3)
        return {
            "enabled": self._enabled,
            "description": self._transport.description if self._transport else None,
            "connected": connected,
            "last_ok_age_s": age,
            "last_error": self._last_error,
            "fault": self._fault,
            **self.stats(),
        }

    async def ping(self) -> dict:
        """One PING, on demand, waiting for the reply.

        The button an operator taps after typing an address — the passive
        indicator needs up to a heartbeat interval to notice a change, and
        someone standing at a booth with a screwdriver should not have to wait
        for it.
        """
        if not self._enabled:
            return {"ok": False, "reply": None, "elapsed_ms": None,
                    "detail": "LED ring is disabled"}

        started = time.perf_counter()
        reply = await self._submit("PING", wait=True)
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)
        if reply is None:
            return {"ok": False, "reply": None, "elapsed_ms": elapsed_ms,
                    "detail": self._last_error or "no reply"}
        return {"ok": self._is_ack("PING", reply), "reply": reply,
                "elapsed_ms": elapsed_ms, "detail": None}

    # --- command plumbing --------------------------------------------------

    async def _submit_screen(self, line: str) -> None:
        """Queue a screen command, unless a fault is currently showing.

        Recorded either way, so set_fault(None) can restore the right one.
        """
        self._screen_line = line
        if self._fault is not None:
            return
        await self._submit(line)

    async def _submit(self, line: str, timeout_s: Optional[float] = None, *,
                      wait: bool = False) -> Optional[str]:
        """Queue a line for the owner task.

        With `wait=False` (the default) this returns as soon as the line is
        queued — the send is still serialized by the owner task, the caller just
        does not block on the round trip. Only CAPTURE has a reason to wait.

        Returns None when disabled, when not waiting, or when the link failed.
        Callers treat that as "the ring did not do it", never as an error to
        propagate.
        """
        if not self._enabled or self._queue is None:
            return None

        loop = asyncio.get_running_loop()
        future: Optional[asyncio.Future] = loop.create_future() if wait else None
        await self._queue.put(_Request(line, timeout_s or self._default_timeout_s, future))
        if future is None:
            return None
        try:
            return await future
        except LedLinkError:
            return None
        except asyncio.CancelledError:
            raise

    # --- booth semantics ---------------------------------------------------

    async def idle(self) -> None:
        await self._submit_screen("IDLE")

    async def phase(self, hue: int) -> None:
        await self._submit_screen(f"PHASE {int(hue) % 360}")

    async def ready(self) -> None:
        """Park the ring, poised, for the beat before the count starts.

        That beat is the camera warming up, whose length only the browser can
        observe, so nothing here or in the FSM can predict it.
        """
        await self._submit_screen("READY")

    async def countdown(self, duration_ms: int) -> None:
        # Sent when the guest-visible count actually starts, not when the FSM
        # enters the countdown screen — see state_machine's COUNTDOWN_STARTED.
        # Both sides then run the same duration from the same instant; the
        # firmware still clamps elapsed to duration, so any residual skew
        # degrades to a frozen head rather than a glitch (anim_countdown.c).
        await self._submit_screen(f"COUNTDOWN {_clamp_ms(duration_ms)}")

    async def capture(self) -> bool:
        """Take the ring to full white. True if the node acknowledged.

        **The caller must wait for this before firing the shutter** — the ring is
        the key light, and firing early photographs it mid-ramp.

        False means the ring may or may not be lit: a transport timeout and an
        `ERR TIMEOUT` reply are both ambiguous by nature, and neither can be
        resolved without another round trip on a link that just proved slow.
        """
        reply = await self._submit("CAPTURE", self._capture_timeout_s, wait=True)
        return reply is not None and self._is_ack("CAPTURE", reply)

    async def release(self) -> None:
        await self._submit("RELEASE")

    async def printing(self) -> None:
        await self._submit_screen("PRINTING")

    async def finished(self, duration_ms: int) -> None:
        await self._submit_screen(f"FINISHED {_clamp_ms(duration_ms)}")

    async def error(self, code: int = 1) -> None:
        await self._submit(f"ERROR {max(0, int(code))}")

    # --- bench instrument ---------------------------------------------------

    async def test_channel(self, channel: int) -> Optional[str]:
        """Light one physical die across the whole ring, and wait for the reply.

        Not a booth semantic — an operator holding a screwdriver. Waits, because
        the person who tapped the button needs to know whether the node took it,
        and unlike a screen command there is no next transition to reveal that.

        Not routed through _submit_screen: this deliberately overrides whatever
        is showing, including a latched fault. Diagnosing the strip is the one
        job that outranks reporting that something else is broken.
        """
        return await self._submit(f"TEST {max(0, int(channel))}",
                                  self._capture_timeout_s, wait=True)


def create_led_controller(settings, fault_source=None) -> LedController:
    """Build the controller from config. Called by the composition root only.

    Returns an inert controller rather than None when the ring is disabled or
    misconfigured, so no call site needs a null check and the booth behaves
    identically either way.

    `fault_source` is an optional callable returning an ERROR code, or None when
    the booth is healthy. The composition root supplies it; nothing here knows
    what a fault is made of.
    """
    cfg = settings.led

    return LedController(
        _build_transport(cfg),
        enabled=cfg.enabled,
        heartbeat_s=cfg.heartbeat_ms / 1000.0,
        capture_timeout_s=cfg.http.capture_timeout_ms / 1000.0,
        default_timeout_s=cfg.http.timeout_ms / 1000.0,
        config_key=_config_key(cfg),
        fault_source=fault_source,
    )


def _config_key(cfg) -> tuple:
    """Everything about a config block that the controller actually behaves on.

    Compared by reconfigure() to tell a save that touched the ring from one that
    changed the couple's names.
    """
    return (cfg.enabled, cfg.transport, cfg.http.host.strip(),
            cfg.http.timeout_ms, cfg.http.capture_timeout_ms, cfg.heartbeat_ms)


def _build_transport(cfg) -> Optional[LedTransport]:
    """The transport for this config, or None when the ring cannot be reached.

    Shared by the composition root and reconfigure() so there is one place that
    decides what a given config block means. When UART lands this is the only
    function that grows a branch (Docs/LED_UART_SWITCH.md).
    """
    if not cfg.enabled:
        return None
    if cfg.transport == "http" and cfg.http.host.strip():
        return LedHttpTransport(cfg.http.host,
                                timeout_s=cfg.http.timeout_ms / 1000.0)
    log.warn("led", "led_misconfigured",
             f"LED enabled but unusable (transport={cfg.transport!r}, "
             f"host={cfg.http.host!r}) — running without a ring")
    return None


def _clamp_ms(value: int) -> int:
    """Hold arguments inside the protocol's 1..60000 range.

    Out-of-range values earn `ERR RANGE` and leave the mode unchanged, so a
    miscomputed duration would silently strand the ring in whatever it was
    showing. Clamping fails visibly instead.
    """
    return max(1, min(60000, int(value)))


def _percentile(ordered: list, q: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]

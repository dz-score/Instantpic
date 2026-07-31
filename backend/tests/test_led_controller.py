import asyncio

import pytest

from backend.led_controller import LedController
from backend.led_transport import LedLinkDown, LedLinkTimeout


@pytest.fixture
def anyio_backend():
    return 'asyncio'


class FakeTransport:
    """Records the lines it was asked to carry and replays scripted replies.

    The controller takes its transport as a constructor argument, so a double
    goes straight in — no monkeypatching, and no HTTP server in a unit test.
    """

    description = "fake://led"

    def __init__(self, replies=None, fail_with=None, delay_s=0.0):
        self.sent = []
        self.started = False
        self.stopped = False
        self.resyncs = 0
        self._replies = dict(replies or {})
        self._fail_with = fail_with
        self._delay_s = delay_s
        self.concurrent = 0
        self.max_concurrent = 0

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def send(self, line, timeout_s):
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            if self._delay_s:
                await asyncio.sleep(self._delay_s)
            self.sent.append(line)
            if self._fail_with is not None:
                raise self._fail_with
            verb = line.split(" ", 1)[0].upper()
            if line in self._replies:
                return self._replies[line]
            if verb in self._replies:
                return self._replies[verb]
            return "PONG" if verb == "PING" else f"OK {verb}"
        finally:
            self.concurrent -= 1

    async def resync(self):
        self.resyncs += 1


def make(transport, **kw):
    kw.setdefault("enabled", True)
    kw.setdefault("heartbeat_s", 60.0)  # long, so it never fires mid-test
    kw.setdefault("capture_timeout_s", 0.25)
    kw.setdefault("default_timeout_s", 0.4)
    return LedController(transport, **kw)


@pytest.mark.anyio
async def test_commands_map_to_protocol_lines():
    t = FakeTransport()
    led = make(t)
    await led.start()

    await led.idle()
    await led.phase(280)
    await led.countdown(3000)
    await led.printing()
    await led.finished(4000)
    await led.error(2)
    await led.release()
    await led.stop()

    assert t.sent == ["IDLE", "PHASE 280", "COUNTDOWN 3000", "PRINTING",
                      "FINISHED 4000", "ERROR 2", "RELEASE"]


@pytest.mark.anyio
async def test_capture_returns_true_only_on_ack():
    t = FakeTransport(replies={"CAPTURE": "OK CAPTURE"})
    led = make(t)
    await led.start()
    assert await led.capture() is True
    await led.stop()


@pytest.mark.anyio
async def test_capture_false_on_err_timeout():
    """ERR TIMEOUT is a reply, not a failure — and it is ambiguous.

    The node may or may not have applied the command. capture() must report it
    as unacknowledged so the FSM can make the call, rather than swallowing it.
    """
    t = FakeTransport(replies={"CAPTURE": "ERR TIMEOUT"})
    led = make(t)
    await led.start()
    assert await led.capture() is False
    await led.stop()


@pytest.mark.anyio
async def test_capture_false_when_link_down():
    t = FakeTransport(fail_with=LedLinkDown("no route"))
    led = make(t)
    await led.start()
    assert await led.capture() is False
    await led.stop()


@pytest.mark.anyio
async def test_link_failure_never_raises_into_the_booth():
    """A dead ring must not stop the booth taking photos."""
    t = FakeTransport(fail_with=LedLinkTimeout("silent"))
    led = make(t)
    await led.start()

    await led.idle()
    await led.countdown(3000)
    assert await led.capture() is False
    await led.release()
    await led.stop()


@pytest.mark.anyio
async def test_link_failure_triggers_resync():
    """The hook that keeps a UART swap a port rather than a redesign."""
    t = FakeTransport(fail_with=LedLinkDown("boom"))
    led = make(t)
    await led.start()
    await led.idle()
    await led.stop()

    assert t.resyncs == 1


@pytest.mark.anyio
async def test_only_one_command_in_flight():
    """The invariant the firmware's depth-1 reply queue actually requires.

    HTTP would forgive overlapping sends; a byte-stream transport would not.
    Ten concurrent callers must still reach the transport strictly one at a time.
    """
    t = FakeTransport(delay_s=0.01)
    led = make(t)
    await led.start()

    await asyncio.gather(*(led.phase(h) for h in range(10)))
    await led.stop()

    assert t.max_concurrent == 1
    assert len(t.sent) == 10


@pytest.mark.anyio
async def test_disabled_controller_is_inert_but_does_not_block_capture():
    t = FakeTransport()
    led = make(t, enabled=False)
    await led.start()

    await led.idle()
    # False: nothing was acknowledged, because nothing was sent. The FSM still
    # fires the shutter — a booth with no ring configured must keep working.
    assert await led.capture() is False
    await led.stop()

    assert t.sent == []
    assert t.started is False


@pytest.mark.anyio
async def test_none_transport_disables_controller():
    led = LedController(None, enabled=True, heartbeat_s=60.0,
                        capture_timeout_s=0.25, default_timeout_s=0.4)
    assert led.enabled is False
    await led.start()
    await led.idle()
    await led.stop()


@pytest.mark.anyio
async def test_heartbeat_fires_only_when_the_wire_is_idle():
    """The watchdog counts any inbound line, so a busy wire needs no PING.

    A fixed timer would also risk queueing a PING ahead of CAPTURE in the
    countdown window, which is the one place latency is visible.
    """
    t = FakeTransport()
    led = make(t, heartbeat_s=0.05)
    await led.start()

    # Busy: a command every 20 ms, well inside the heartbeat interval.
    for _ in range(5):
        await led.idle()
        await asyncio.sleep(0.02)
    assert "PING" not in t.sent

    # Idle: nothing sent, so the heartbeat takes over.
    await asyncio.sleep(0.12)
    await led.stop()
    assert "PING" in t.sent


@pytest.mark.anyio
async def test_arguments_are_clamped_into_protocol_range():
    """Out-of-range values earn ERR RANGE and leave the mode unchanged.

    A miscomputed duration would silently strand the ring in whatever it was
    showing, so clamping fails visibly instead.
    """
    t = FakeTransport()
    led = make(t)
    await led.start()

    await led.countdown(0)
    await led.countdown(999_999)
    await led.phase(400)
    await led.stop()

    assert t.sent == ["COUNTDOWN 1", "COUNTDOWN 60000", "PHASE 40"]


@pytest.mark.anyio
async def test_stats_report_capture_latency_percentiles():
    """This is the evidence the HTTP-vs-UART decision rests on."""
    t = FakeTransport(delay_s=0.005)
    led = make(t)
    await led.start()
    for _ in range(4):
        await led.capture()
    await led.stop()

    stats = led.stats()
    assert stats["enabled"] is True
    assert stats["latency_ms"]["CAPTURE"]["n"] == 4
    assert stats["latency_ms"]["CAPTURE"]["p99"] >= stats["latency_ms"]["CAPTURE"]["p50"]
    assert stats["counts"]["sent:CAPTURE"] == 4

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


# --- live reconfiguration -------------------------------------------------


def _settings(**led):
    """AppSettings with the led block overridden. http keys go under `http`."""
    from backend.settings import AppSettings
    http = led.pop("http", {})
    return AppSettings(led={"enabled": True, "http": {"host": "10.0.0.9", **http},
                            **led})


@pytest.fixture
def transports(monkeypatch):
    """Hands out a fresh FakeTransport per _build_transport call, in order."""
    import backend.led_controller as mod
    made = []

    def build(cfg):
        if not cfg.enabled or not cfg.http.host.strip():
            return None
        made.append(FakeTransport())
        return made[-1]

    monkeypatch.setattr(mod, "_build_transport", build)
    return made


@pytest.mark.anyio
async def test_reconfigure_swaps_the_transport(transports):
    """The controller must keep its identity — the FSM holds this reference for
    the life of the process, so a rebuild would leave the booth driving a
    stopped object."""
    from backend.led_controller import create_led_controller

    led = create_led_controller(_settings())
    await led.start()
    await led.idle()

    changed = await led.reconfigure(_settings(http={"host": "10.0.0.42"}))
    await led.phase(120)
    await led.stop()

    assert changed is True
    assert len(transports) == 2
    assert transports[0].stopped is True
    assert transports[0].sent == ["IDLE"]
    assert transports[1].sent == ["PHASE 120"]


@pytest.mark.anyio
async def test_unrelated_config_change_does_not_bounce_the_link(transports):
    """Most admin saves are about the couple's names. A venue is the worst place
    to drop a working connection for no reason."""
    from backend.led_controller import create_led_controller

    led = create_led_controller(_settings())
    await led.start()

    base = _settings()
    base.couple_names = "Someone Else"
    changed = await led.reconfigure(base)

    assert changed is False
    assert len(transports) == 1
    assert transports[0].stopped is False
    await led.stop()


@pytest.mark.anyio
async def test_reconfigure_to_disabled_leaves_the_controller_inert(transports):
    from backend.led_controller import create_led_controller

    led = create_led_controller(_settings())
    await led.start()

    await led.reconfigure(_settings(enabled=False))

    assert led.enabled is False
    assert transports[0].stopped is True
    # Still callable, still never raises into the booth, and still honest about
    # not having lit anything.
    await led.idle()
    assert await led.capture() is False
    await led.stop()


@pytest.mark.anyio
async def test_reconfigure_keeps_the_latency_history(transports):
    """A link reconfigured because it was misbehaving is exactly the one whose
    history matters (Docs/LED_UART_SWITCH.md)."""
    from backend.led_controller import create_led_controller

    led = create_led_controller(_settings())
    await led.start()
    await led.capture()

    await led.reconfigure(_settings(http={"host": "10.0.0.42"}))
    await led.stop()

    assert led.stats()["counts"]["sent:CAPTURE"] == 1
    assert led.stats()["latency_ms"]["CAPTURE"]["n"] == 1


# --- health and the on-demand probe ---------------------------------------


@pytest.mark.anyio
async def test_health_reports_connected_after_a_successful_exchange():
    t = FakeTransport()
    led = make(t)
    await led.start()
    await led.idle()
    await led.drain()

    h = led.health()
    assert h["connected"] is True
    assert h["last_error"] is None
    assert h["description"] == "fake://led"
    await led.stop()


@pytest.mark.anyio
async def test_health_reports_the_link_error_and_goes_disconnected():
    t = FakeTransport(fail_with=LedLinkDown("no route to host"))
    led = make(t)
    await led.start()
    await led.idle()
    await led.drain()

    h = led.health()
    assert h["connected"] is False
    assert "no route to host" in h["last_error"]
    await led.stop()


@pytest.mark.anyio
async def test_health_goes_stale_when_the_heartbeat_stops_landing():
    """connected is inferred from recent traffic, not probed. Three missed
    heartbeat intervals is the allowance."""
    t = FakeTransport()
    led = make(t, heartbeat_s=0.02)
    await led.start()
    await led.idle()
    await led.drain()
    assert led.health()["connected"] is True

    await led.stop()          # heartbeat stops with the owner task
    await asyncio.sleep(0.1)  # > 3x the interval
    assert led.health()["connected"] is False


@pytest.mark.anyio
async def test_ping_reports_the_round_trip():
    t = FakeTransport()
    led = make(t)
    await led.start()

    result = await led.ping()
    await led.stop()

    assert result["ok"] is True
    assert result["reply"] == "PONG"
    assert result["elapsed_ms"] is not None
    assert t.sent == ["PING"]


@pytest.mark.anyio
async def test_ping_reports_an_unreachable_node_without_raising():
    t = FakeTransport(fail_with=LedLinkTimeout("no reply"))
    led = make(t)
    await led.start()

    result = await led.ping()
    await led.stop()

    assert result["ok"] is False
    assert result["reply"] is None
    assert "no reply" in result["detail"]


@pytest.mark.anyio
async def test_ping_on_a_disabled_ring_says_so():
    led = make(None, enabled=False)
    await led.start()
    result = await led.ping()
    assert result["ok"] is False
    assert "disabled" in result["detail"]

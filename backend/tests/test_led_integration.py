"""LED wiring: the FSM's side of the ring, and the HTTP transport's own contract.

The controller's internals are covered in test_led_controller.py. This file
covers the two seams that file cannot: what the state machine actually asks the
ring to do, and whether LedHttpTransport speaks the protocol correctly.
"""

import httpx
import pytest

from backend.led_transport import LedHttpTransport, LedLinkDown, LedLinkTimeout
from backend.settings import AppSettings
from backend.state_machine import SCREEN_HUE, StateMachine, countdown_ms
from backend.tests.test_state_machine import FakeSse, MockCamera, MockQueue


@pytest.fixture
def anyio_backend():
    return 'asyncio'


SETTINGS = AppSettings()


class RecordingLed:
    """Records the semantic calls the FSM makes, in order.

    Also records interleaving with the camera, because the ordering of
    capture() against enqueue_capture() is the whole point of the ack.
    """

    enabled = True

    def __init__(self, capture_acked=True, log=None):
        self.calls = []
        self.log = log if log is not None else []
        self._capture_acked = capture_acked

    async def idle(self):
        self.calls.append("IDLE")
        self.log.append("led:IDLE")

    async def phase(self, hue):
        self.calls.append(f"PHASE {hue}")
        self.log.append("led:PHASE")

    async def countdown(self, duration_ms):
        self.calls.append(f"COUNTDOWN {duration_ms}")
        self.log.append("led:COUNTDOWN")

    async def capture(self):
        self.calls.append("CAPTURE")
        self.log.append("led:CAPTURE")
        return self._capture_acked

    async def release(self):
        self.calls.append("RELEASE")
        self.log.append("led:RELEASE")

    async def printing(self):
        self.calls.append("PRINTING")
        self.log.append("led:PRINTING")

    async def finished(self, duration_ms):
        self.calls.append(f"FINISHED {duration_ms}")
        self.log.append("led:FINISHED")

    async def error(self, code=1):
        self.calls.append(f"ERROR {code}")
        self.log.append("led:ERROR")


class OrderedCamera(MockCamera):
    """MockCamera that also appends to a shared ordering log."""

    def __init__(self, log):
        super().__init__()
        self.log = log

    def enqueue_capture(self, on_complete=None, on_failure=None):
        self.log.append("camera:shutter")
        return super().enqueue_capture(on_complete=on_complete, on_failure=on_failure)


def make_sm(led=None):
    q, cam, sse = MockQueue(), MockCamera(), FakeSse()
    led = led or RecordingLed()
    return StateMachine(sse, q, cam, led), q, cam, led


# --- what the FSM asks the ring to do ------------------------------------


@pytest.mark.anyio
async def test_ring_is_lit_before_the_shutter_fires():
    """The ordering that justifies the whole ack.

    At Capture the ring is the key light. Firing first photographs it mid-ramp.
    """
    order = []
    led = RecordingLed(log=order)
    cam = OrderedCamera(order)
    sm = StateMachine(FakeSse(), MockQueue(), cam, led)

    await sm.handle_event("START_SESSION", {}, SETTINGS)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"}, SETTINGS)
    await sm.handle_event("FIRE_SHOT", {}, SETTINGS)

    assert order.index("led:CAPTURE") < order.index("camera:shutter")


@pytest.mark.anyio
async def test_shutter_still_fires_when_the_ring_does_not_acknowledge():
    """A dim photo beats no photo, and the guest can retake.

    False is 'unacknowledged', not 'definitely dark' — the reply is ambiguous.
    """
    led = RecordingLed(capture_acked=False)
    sm, q, cam, _ = make_sm(led)

    await sm.handle_event("START_SESSION", {}, SETTINGS)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"}, SETTINGS)
    await sm.handle_event("FIRE_SHOT", {}, SETTINGS)

    assert len(cam.pending) == 1


@pytest.mark.anyio
async def test_countdown_duration_matches_what_the_browser_shows():
    led = RecordingLed()
    sm, q, cam, _ = make_sm(led)
    settings = AppSettings(countdown_duration=5, countdown_speed=2.0)

    await sm.handle_event("START_SESSION", {}, settings)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"}, settings)

    assert countdown_ms(settings) == 2500
    assert "COUNTDOWN 2500" in led.calls


@pytest.mark.anyio
async def test_screen_transitions_drive_the_ring():
    led = RecordingLed()
    sm, q, cam, _ = make_sm(led)

    await sm.handle_event("START_SESSION", {}, SETTINGS)
    assert f"PHASE {SCREEN_HUE['CHOOSE_STYLE']}" in led.calls

    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"}, SETTINGS)
    assert any(c.startswith("COUNTDOWN") for c in led.calls)

    await sm.handle_event("TIMEOUT", {}, SETTINGS)
    assert led.calls[-1] == "IDLE"


@pytest.mark.anyio
async def test_capture_is_released_when_the_shot_fails():
    """No transition follows a failed shot, so nothing else takes the ring out
    of full white — the highest-current, highest-heat state in the system."""
    led = RecordingLed()
    sm, q, cam, _ = make_sm(led)

    await sm.handle_event("START_SESSION", {}, SETTINGS)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"}, SETTINGS)
    await sm.handle_event("FIRE_SHOT", {}, SETTINGS)
    await cam.fail("shutter jammed")

    assert led.calls[-1] == "RELEASE"


@pytest.mark.anyio
async def test_completed_shot_leaves_capture_without_an_explicit_release():
    """Reveal's hue takes the ring out of full white on its own."""
    led = RecordingLed()
    sm, q, cam, _ = make_sm(led)

    await sm.handle_event("START_SESSION", {}, SETTINGS)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"}, SETTINGS)
    await sm.handle_event("FIRE_SHOT", {}, SETTINGS)
    await cam.complete("shot1.jpg")

    assert led.calls[-1] == f"PHASE {SCREEN_HUE['REVEAL']}"


@pytest.mark.anyio
async def test_ring_enters_printing_on_the_frame_processed_path():
    """PRINTING is reached from a job callback, not from handle_event.

    That callback is the one path that lands the guest on a new screen without
    going through the transition that normally syncs the ring, so without an
    explicit sync there the ring would hold the frame-picker hue for the whole
    print.
    """
    led = RecordingLed()
    sm, q, cam, _ = make_sm(led)

    await sm.handle_event("START_SESSION", {}, SETTINGS)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"}, SETTINGS)
    await sm.handle_event("FIRE_SHOT", {}, SETTINGS)
    await cam.complete("shot1.jpg")
    await sm.handle_event("PRINT_FROM_REVEAL", {}, SETTINGS)
    assert (await sm.get_state()).screen == "FRAME_PICKER"

    await sm.handle_event("FRAME_SELECT", {"overlay_id": "blush_floral"}, SETTINGS)
    await sm.job_frame_processed("final.jpg")

    assert (await sm.get_state()).screen == "PRINTING"
    assert led.calls[-1] == "PRINTING"


@pytest.mark.anyio
async def test_booth_runs_with_no_ring_injected():
    """The default. StateMachine substitutes an inert stand-in."""
    sm = StateMachine(FakeSse(), MockQueue(), MockCamera())

    await sm.handle_event("START_SESSION", {}, SETTINGS)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"}, SETTINGS)
    await sm.handle_event("FIRE_SHOT", {}, SETTINGS)

    state = await sm.get_state()
    assert state.screen == "COUNTDOWN"


# --- the HTTP transport's own contract ------------------------------------


def transport_against(handler):
    t = LedHttpTransport("10.0.0.5", timeout_s=0.5)
    t._client = httpx.AsyncClient(base_url="http://10.0.0.5",
                                  transport=httpx.MockTransport(handler))
    return t


@pytest.mark.anyio
async def test_err_replies_arrive_as_http_200_and_are_returned_verbatim():
    """The detail most likely to produce a broken client.

    The node answers ERR with a 200 — the request was well-formed, the command
    was not. A transport that inferred success from the status code would report
    every rejected command as accepted, CAPTURE included.
    """
    def handler(request):
        assert request.url.path == "/cmd"
        assert request.method == "POST"
        return httpx.Response(200, text="ERR RANGE")

    t = transport_against(handler)
    assert await t.send("PHASE 400", 0.5) == "ERR RANGE"
    await t.stop()


@pytest.mark.anyio
async def test_command_line_is_the_raw_body():
    seen = {}

    def handler(request):
        seen["body"] = request.content.decode()
        return httpx.Response(200, text="OK COUNTDOWN")

    t = transport_against(handler)
    await t.send("COUNTDOWN 3000", 0.5)
    await t.stop()

    assert seen["body"] == "COUNTDOWN 3000"


@pytest.mark.anyio
async def test_timeout_and_unreachable_are_distinguishable():
    def timing_out(request):
        raise httpx.ReadTimeout("too slow", request=request)

    t = transport_against(timing_out)
    with pytest.raises(LedLinkTimeout):
        await t.send("PING", 0.5)
    await t.stop()

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    t = transport_against(refused)
    with pytest.raises(LedLinkDown):
        await t.send("PING", 0.5)
    await t.stop()


@pytest.mark.anyio
async def test_unexpected_status_is_a_link_fault_not_a_reply():
    """Every documented reply is a 200, so anything else is the server
    misbehaving rather than the command being refused."""
    t = transport_against(lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(LedLinkDown):
        await t.send("IDLE", 0.5)
    await t.stop()

import pytest
import asyncio
from backend.state_machine import StateMachine
from backend.settings import AppSettings, OverlayConfig

@pytest.fixture
def anyio_backend():
    return 'asyncio'


DEFAULT_SETTINGS = AppSettings()

NO_FRAME_SETTINGS = AppSettings(overlays=[OverlayConfig(id="none", name="No Frame", filename="")])
"""Config with only the 'none' overlay -> print flow skips the frame picker."""

FRAMED_SETTINGS = AppSettings()
"""Default config ships real frames -> print flow routes to the frame picker."""


class FakeSse:
    """Records dispatches instead of pushing them to browsers.

    The FSM takes its SSE service as a constructor argument, so a double goes
    straight in — no monkeypatching a module global to intercept broadcasts.
    """
    def __init__(self):
        self.payloads = []
    def dispatch_event(self, event, data):
        self.payloads.append(data)


class MockQueue:
    def __init__(self):
        self.last_job = None
    async def enqueue(self, job):
        self.last_job = job


class MockCamera:
    """Test double for CameraService.enqueue_capture's contract: the FSM
    hands over terminal callbacks; the test invokes them to simulate the
    camera worker finishing."""
    def __init__(self):
        self.pending = []
    def enqueue_capture(self, on_complete=None, on_failure=None):
        self.pending.append((on_complete, on_failure))
        return f"job{len(self.pending)}"
    async def complete(self, filename):
        on_complete, _ = self.pending.pop(0)
        await on_complete(filename)
    async def fail(self, error):
        _, on_failure = self.pending.pop(0)
        await on_failure(error)


def make_sm():
    q, cam, sse = MockQueue(), MockCamera(), FakeSse()
    return StateMachine(sse, q, cam), q, cam, sse


async def fire_and_complete(sm, cam, filename, settings):
    """One full shot: UI fires, camera worker completes via callback."""
    await sm.handle_event("FIRE_SHOT", {}, settings)
    await cam.complete(filename)


@pytest.mark.anyio
async def test_initial_state():
    sm, q, cam, sse = make_sm()
    state = await sm.get_state()
    assert state.screen == "ATTRACT"
    assert state.isProcessing is False

@pytest.mark.anyio
async def test_start_session():
    sm, q, cam, sse = make_sm()
    await sm.handle_event("START_SESSION", {}, DEFAULT_SETTINGS)
    state = await sm.get_state()
    assert state.screen == "CHOOSE_STYLE"

@pytest.mark.anyio
async def test_layout_sets_shot_count():
    sm, q, cam, sse = make_sm()
    await sm.handle_event("START_SESSION", {}, DEFAULT_SETTINGS)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "collage"}, DEFAULT_SETTINGS)
    state = await sm.get_state()
    assert state.screen == "COUNTDOWN"
    assert state.layoutMode == "collage"
    # Shot count is a workflow rule owned by the FSM, not the UI.
    assert state.totalShots == 3

@pytest.mark.anyio
async def test_full_flow_no_frames():
    sm, q, cam, sse = make_sm()

    await sm.handle_event("START_SESSION", {}, NO_FRAME_SETTINGS)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "collage"}, NO_FRAME_SETTINGS)

    # Each shot lands via the camera callback; the FSM appends it first-hand
    # and only advances to REVEAL on the last one.
    await fire_and_complete(sm, cam, "img1", NO_FRAME_SETTINGS)
    state = await sm.get_state()
    assert state.screen == "COUNTDOWN"
    assert state.capturedImages == ["img1"]

    await fire_and_complete(sm, cam, "img2", NO_FRAME_SETTINGS)
    state = await sm.get_state()
    assert state.screen == "COUNTDOWN"

    await fire_and_complete(sm, cam, "img3", NO_FRAME_SETTINGS)
    state = await sm.get_state()
    assert state.screen == "REVEAL"
    assert state.isProcessing is True
    assert q.last_job["type"] == "PROCESS_PHOTO"
    assert q.last_job["images"] == ["img1", "img2", "img3"]

    await sm.job_photo_processed("photo.jpg", ["img1", "img2", "img3"])
    state = await sm.get_state()
    assert state.isProcessing is False
    assert state.finalPhoto == "photo.jpg"
    assert len(state.allSessionPhotos) == 1

    # No real frames configured -> straight to PRINTING (routing read from config).
    await sm.handle_event("PRINT_FROM_REVEAL", {}, NO_FRAME_SETTINGS)
    state = await sm.get_state()
    assert state.screen == "PRINTING"

    await sm.handle_event("FINISH", {}, NO_FRAME_SETTINGS)
    state = await sm.get_state()
    assert state.screen == "ATTRACT"

@pytest.mark.anyio
async def test_retake_and_pick_favorite():
    sm, q, cam, sse = make_sm()
    await sm.handle_event("START_SESSION", {}, NO_FRAME_SETTINGS)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"}, NO_FRAME_SETTINGS)

    await fire_and_complete(sm, cam, "img1", NO_FRAME_SETTINGS)
    await sm.job_photo_processed("p1.jpg", ["img1"])

    await sm.handle_event("RETAKE", {}, NO_FRAME_SETTINGS)
    state = await sm.get_state()
    assert state.screen == "COUNTDOWN"
    assert state.retakeCount == 1
    assert state.capturedImages == []

    await fire_and_complete(sm, cam, "img2", NO_FRAME_SETTINGS)
    await sm.job_photo_processed("p2.jpg", ["img2"])

    # User has multiple photos, printing from reveal goes to pick favorite
    await sm.handle_event("PRINT_FROM_REVEAL", {}, NO_FRAME_SETTINGS)
    state = await sm.get_state()
    assert state.screen == "PICK_FAVORITE"

    # Pick the first one (no frames configured -> straight to PRINTING)
    await sm.handle_event("FAVORITE_SELECT", {"filename": "p1.jpg"}, NO_FRAME_SETTINGS)
    state = await sm.get_state()
    assert state.screen == "PRINTING"
    assert state.finalPhoto == "p1.jpg"

@pytest.mark.anyio
async def test_frame_picker_flow():
    sm, q, cam, sse = make_sm()

    await sm.handle_event("START_SESSION", {}, FRAMED_SETTINGS)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"}, FRAMED_SETTINGS)
    await fire_and_complete(sm, cam, "img1", FRAMED_SETTINGS)
    await sm.job_photo_processed("p1.jpg", ["img1"])

    # Default config ships real frames -> routing lands on the frame picker.
    await sm.handle_event("PRINT_FROM_REVEAL", {}, FRAMED_SETTINGS)
    state = await sm.get_state()
    assert state.screen == "FRAME_PICKER"

    await sm.handle_event("FRAME_SELECT", {"overlay_id": "gold_glitter"}, FRAMED_SETTINGS)
    state = await sm.get_state()
    assert state.isProcessing is True
    assert q.last_job["type"] == "PROCESS_FRAME"
    assert q.last_job["overlay_id"] == "gold_glitter"

    await sm.job_frame_processed("framed.jpg")
    state = await sm.get_state()
    assert state.screen == "PRINTING"
    assert state.finalPhoto == "framed.jpg"

@pytest.mark.anyio
async def test_entering_printing_kicks_off_print_job():
    """Print is backend-owned: entering PRINTING enqueues a PRINT_PHOTO job and
    marks printStatus 'printing' — the UI never triggers the print itself."""
    sm, q, cam, sse = make_sm()

    await sm.handle_event("START_SESSION", {}, NO_FRAME_SETTINGS)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"}, NO_FRAME_SETTINGS)
    await fire_and_complete(sm, cam, "img1", NO_FRAME_SETTINGS)
    await sm.job_photo_processed("p1.jpg", ["img1"])

    await sm.handle_event("PRINT_FROM_REVEAL", {}, NO_FRAME_SETTINGS)
    state = await sm.get_state()
    assert state.screen == "PRINTING"
    assert state.printStatus == "printing"
    assert q.last_job["type"] == "PRINT_PHOTO"
    assert q.last_job["filename"] == "p1.jpg"


@pytest.mark.anyio
async def test_print_outcome_projected_from_backend():
    """The FSM records the real printer outcome; success and failure are distinct
    states the UI can render instead of guessing from a timeout."""
    sm, q, cam, sse = make_sm()
    sm._state.screen = "PRINTING"
    sm._state.printStatus = "printing"

    await sm.job_print_done("p1.jpg")
    assert (await sm.get_state()).printStatus == "printed"

    await sm.job_print_failed("printer offline")
    assert (await sm.get_state()).printStatus == "failed"


@pytest.mark.anyio
async def test_frame_skip_enters_printing():
    """FRAME_SKIP prints the already-processed photo through the same path."""
    sm, q, cam, sse = make_sm()
    sm._state.screen = "FRAME_PICKER"
    sm._state.finalPhoto = "p1.jpg"

    await sm.handle_event("FRAME_SKIP", {}, FRAMED_SETTINGS)
    state = await sm.get_state()
    assert state.screen == "PRINTING"
    assert state.printStatus == "printing"
    assert q.last_job["type"] == "PRINT_PHOTO"


@pytest.mark.anyio
async def test_job_failure_recovery():
    sm, q, cam, sse = make_sm()
    await sm.handle_event("START_SESSION", {}, DEFAULT_SETTINGS)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"}, DEFAULT_SETTINGS)
    await fire_and_complete(sm, cam, "img1", DEFAULT_SETTINGS)

    state = await sm.get_state()
    assert state.isProcessing is True

    await sm.job_failed("Oops")
    state = await sm.get_state()
    assert state.isProcessing is False
    # Still on reveal so they can retake or something
    assert state.screen == "REVEAL"


# --- FIRE_SHOT / capture-callback seam ---

@pytest.mark.anyio
async def test_fire_shot_in_flight_guard():
    """A second FIRE_SHOT while a capture is pending must not enqueue a
    second capture; the guard clears once the shot completes."""
    sm, q, cam, sse = make_sm()
    await sm.handle_event("START_SESSION", {}, DEFAULT_SETTINGS)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "collage"}, DEFAULT_SETTINGS)

    await sm.handle_event("FIRE_SHOT", {}, DEFAULT_SETTINGS)
    await sm.handle_event("FIRE_SHOT", {}, DEFAULT_SETTINGS)  # double-tap
    assert len(cam.pending) == 1

    await cam.complete("img1")
    # Guard released -> next shot fires normally.
    await sm.handle_event("FIRE_SHOT", {}, DEFAULT_SETTINGS)
    assert len(cam.pending) == 1

@pytest.mark.anyio
async def test_shot_failure_keeps_countdown_and_releases_guard():
    """A permanently failed capture leaves the session in COUNTDOWN (the UI
    offers retry/home from the camera_job SSE event) and allows re-firing."""
    sm, q, cam, sse = make_sm()
    await sm.handle_event("START_SESSION", {}, DEFAULT_SETTINGS)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"}, DEFAULT_SETTINGS)

    await sm.handle_event("FIRE_SHOT", {}, DEFAULT_SETTINGS)
    await cam.fail("shutter jammed")

    state = await sm.get_state()
    assert state.screen == "COUNTDOWN"
    assert state.capturedImages == []

    # Retry works: guard was released by the failure.
    await fire_and_complete(sm, cam, "img1", DEFAULT_SETTINGS)
    assert (await sm.get_state()).screen == "REVEAL"

@pytest.mark.anyio
async def test_late_shot_completion_after_session_end_is_dropped():
    """If the session ends (TIMEOUT) while the shutter is busy, the late
    completion must not mutate the fresh session's state."""
    sm, q, cam, sse = make_sm()
    await sm.handle_event("START_SESSION", {}, DEFAULT_SETTINGS)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"}, DEFAULT_SETTINGS)
    await sm.handle_event("FIRE_SHOT", {}, DEFAULT_SETTINGS)

    await sm.handle_event("TIMEOUT", {}, DEFAULT_SETTINGS)  # guest walked away
    await cam.complete("img_late")  # camera finishes anyway

    state = await sm.get_state()
    assert state.screen == "ATTRACT"
    assert state.capturedImages == []


@pytest.mark.anyio
async def test_broadcast_payload_is_transition_snapshot(monkeypatch):
    """Each broadcast must carry the state snapshot taken under the handler
    lock. If a job callback mutates state between a transition and its
    broadcast (simulated here by delaying broadcasts one loop tick), the
    earlier broadcast must NOT leak the later mutation."""
    sm, q, cam, sse = make_sm()
    payloads = sse.payloads

    orig_broadcast = sm.broadcast_state
    async def delayed_broadcast(state_dict):
        await asyncio.sleep(0)  # yield so a concurrent callback can interleave
        await orig_broadcast(state_dict)
    monkeypatch.setattr(sm, "broadcast_state", delayed_broadcast)

    await sm.handle_event("START_SESSION", {}, NO_FRAME_SETTINGS)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"}, NO_FRAME_SETTINGS)
    await sm.handle_event("FIRE_SHOT", {}, NO_FRAME_SETTINGS)
    payloads.clear()

    # The capture completion (-> REVEAL, isProcessing=True) and the job
    # callback (isProcessing=False, finalPhoto set) run concurrently; the
    # callback mutates state before the transition's delayed broadcast fires.
    await asyncio.gather(
        cam.complete("raw1.jpg"),
        sm.job_photo_processed("final.jpg", ["raw1.jpg"]),
    )

    assert len(payloads) == 2
    # First broadcast: the REVEAL transition as it was at transition time.
    assert payloads[0]["screen"] == "REVEAL"
    assert payloads[0]["isProcessing"] is True
    assert payloads[0]["finalPhoto"] is None
    # Second broadcast: the callback's completed state.
    assert payloads[1]["isProcessing"] is False
    assert payloads[1]["finalPhoto"] == "final.jpg"


# --- COUNTDOWN stall watchdog (floor for a browser or camera that died
# mid-session — ordinary capture completion is backend-owned callbacks) ---

STALL_SETTINGS = AppSettings(capture_stall_timeout=0.15)

@pytest.mark.anyio
async def test_stall_watchdog_resets_stranded_countdown():
    """A session stuck in COUNTDOWN with no shot progress must reset to
    ATTRACT after capture_stall_timeout, and broadcast the reset."""
    sm, q, cam, sse = make_sm()

    await sm.handle_event("START_SESSION", {}, STALL_SETTINGS)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"}, STALL_SETTINGS)
    assert (await sm.get_state()).screen == "COUNTDOWN"

    await asyncio.sleep(0.4)

    state = await sm.get_state()
    assert state.screen == "ATTRACT"
    assert sse.payloads[-1]["screen"] == "ATTRACT"  # the reset was broadcast to clients
    # The stall reset is also the floor for a capture whose callback never
    # arrived — the in-flight guard must not leak into the next session.
    assert sm._shot_in_flight is False

@pytest.mark.anyio
async def test_stall_watchdog_rearms_per_shot():
    """Each shot of a multi-shot layout gets a fresh stall window — progress
    mid-sequence must not trip the watchdog, silence after it must."""
    settings = AppSettings(capture_stall_timeout=0.4)
    sm, q, cam, sse = make_sm()

    await sm.handle_event("START_SESSION", {}, settings)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "collage"}, settings)

    # Shot 1 arrives inside the first window -> re-arms the watchdog.
    await asyncio.sleep(0.25)
    await fire_and_complete(sm, cam, "raw1.jpg", settings)

    # Past the ORIGINAL window (0.25+0.25 > 0.4) but inside the re-armed one.
    await asyncio.sleep(0.25)
    state = await sm.get_state()
    assert state.screen == "COUNTDOWN"
    assert state.capturedImages == ["raw1.jpg"]

    # Now go silent past the re-armed window -> genuinely stalled.
    await asyncio.sleep(0.6)
    assert (await sm.get_state()).screen == "ATTRACT"

@pytest.mark.anyio
async def test_countdown_stall_window_does_not_follow_the_guest_to_reveal():
    """Leaving COUNTDOWN must drop the tight capture window. REVEAL is now
    covered too, but by the much longer session floor — so the guest gets no
    spurious reset at capture_stall_timeout."""
    sm, q, cam, sse = make_sm()

    await sm.handle_event("START_SESSION", {}, STALL_SETTINGS)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"}, STALL_SETTINGS)
    await fire_and_complete(sm, cam, "raw1.jpg", STALL_SETTINGS)

    assert (await sm.get_state()).screen == "REVEAL"

    await asyncio.sleep(0.4)  # well past capture_stall_timeout (0.15)
    assert (await sm.get_state()).screen == "REVEAL"


# --- Session floor (the browser's inactivity timer is the precise one; this
# only catches a frontend that can no longer fire it at all) ---

def abandon_settings(monkeypatch, grace=0.15):
    """Settings + grace small enough to test the floor without a long sleep."""
    monkeypatch.setattr(StateMachine, "SESSION_WATCHDOG_GRACE_S", grace)
    return AppSettings(session_timeout=0, capture_stall_timeout=grace)


@pytest.mark.anyio
async def test_session_watchdog_resets_a_browser_that_died_at_reveal(monkeypatch):
    """The gap this closes: a kiosk tab that crashes at REVEAL never sends
    TIMEOUT, and REVEAL has no capture window — the booth used to sit there
    with the previous guest's photos until someone noticed."""
    settings = abandon_settings(monkeypatch)
    sm, q, cam, sse = make_sm()

    await sm.handle_event("START_SESSION", {}, settings)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"}, settings)
    await fire_and_complete(sm, cam, "raw1.jpg", settings)
    assert (await sm.get_state()).screen == "REVEAL"

    await asyncio.sleep(0.4)  # browser is gone; nothing else will end this

    state = await sm.get_state()
    assert state.screen == "ATTRACT"
    assert state.capturedImages == []          # the next guest starts clean
    assert sse.payloads[-1]["screen"] == "ATTRACT"   # and clients were told


@pytest.mark.anyio
@pytest.mark.parametrize("screen", [
    "CHOOSE_STYLE", "COUNTDOWN", "REVEAL", "PICK_FAVORITE", "FRAME_PICKER", "PRINTING",
])
async def test_session_watchdog_covers_every_guest_facing_screen(monkeypatch, screen):
    """The rule is "every screen except ATTRACT", not "the ones we happened to
    think of" — a new screen added later inherits the floor for free."""
    settings = abandon_settings(monkeypatch)
    sm, q, cam, sse = make_sm()

    sm._state.screen = screen
    sm._manage_watchdog(settings)

    await asyncio.sleep(0.4)
    assert (await sm.get_state()).screen == "ATTRACT", f"{screen} was never recovered"


@pytest.mark.anyio
async def test_attract_is_not_watched(monkeypatch):
    """ATTRACT is the resting state — arming there would reset the booth on a
    loop forever while it sits idle waiting for a guest."""
    settings = abandon_settings(monkeypatch)
    sm, q, cam, sse = make_sm()

    sm._manage_watchdog(settings)          # state is ATTRACT out of the box
    assert sm._watchdog is None

    await asyncio.sleep(0.4)
    assert sse.payloads == []              # nothing fired, nothing broadcast


@pytest.mark.anyio
async def test_session_watchdog_is_rearmed_by_activity(monkeypatch):
    """An event proves the frontend is alive, so the floor restarts. Without
    this the booth would reset mid-session on a slow but present guest."""
    settings = abandon_settings(monkeypatch, grace=0.4)
    sm, q, cam, sse = make_sm()

    await sm.handle_event("START_SESSION", {}, settings)   # -> CHOOSE_STYLE

    await asyncio.sleep(0.25)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"}, settings)

    # Past the ORIGINAL window (0.25 + 0.25 > 0.4); the guest is demonstrably
    # still here, so the booth must not have reset under them.
    await asyncio.sleep(0.25)
    assert (await sm.get_state()).screen == "COUNTDOWN"


@pytest.mark.anyio
async def test_session_watchdog_does_not_outrun_the_browser(monkeypatch):
    """The floor must LOSE the race to the browser's own timer. With the real
    grace, a guest sitting at REVEAL well past session_timeout is still the
    frontend's call to end, not the backend's."""
    monkeypatch.setattr(StateMachine, "SESSION_WATCHDOG_GRACE_S", 30)
    settings = AppSettings(session_timeout=0)   # browser would fire immediately
    sm, q, cam, sse = make_sm()

    await sm.handle_event("START_SESSION", {}, settings)
    await asyncio.sleep(0.3)

    # Grace has not elapsed, so the backend keeps its hands off.
    assert (await sm.get_state()).screen == "CHOOSE_STYLE"


# ── REPRINT ──────────────────────────────────────────────────────────────────

def _failed_print(filename="p1.jpg"):
    """A session parked on PRINTING with a print that did not come out."""
    sm, q, cam, sse = make_sm()
    sm._state.screen = "PRINTING"
    sm._state.finalPhoto = filename
    sm._state.printStatus = "failed"
    q.last_job = None
    return sm, q


@pytest.mark.anyio
async def test_reprint_requeues_the_same_photo():
    """The guest was promised this print. After the operator clears the jam they
    should get it, not a new session."""
    sm, q = _failed_print()

    await sm.handle_event("REPRINT", {}, DEFAULT_SETTINGS)

    state = await sm.get_state()
    assert state.screen == "PRINTING"
    assert state.printStatus == "printing"
    assert q.last_job["type"] == "PRINT_PHOTO"
    assert q.last_job["filename"] == "p1.jpg"


@pytest.mark.anyio
async def test_reprint_is_refused_after_a_successful_print():
    """A print that came out and a print that jammed look the same on this
    screen and are not the same situation. Retrying the first spends a second
    sheet of media on a copy nobody agreed to — one print per session."""
    sm, q, cam, sse = make_sm()
    sm._state.screen = "PRINTING"
    sm._state.finalPhoto = "p1.jpg"
    sm._state.printStatus = "printed"
    q.last_job = None

    await sm.handle_event("REPRINT", {}, DEFAULT_SETTINGS)

    assert q.last_job is None
    assert (await sm.get_state()).printStatus == "printed"


@pytest.mark.anyio
async def test_reprint_is_refused_while_a_print_is_still_running():
    """Which is also what makes it idempotent: the first REPRINT moves
    printStatus to 'printing', so a double tap is refused rather than queueing
    a duplicate behind the first."""
    sm, q = _failed_print()

    await sm.handle_event("REPRINT", {}, DEFAULT_SETTINGS)
    first = q.last_job
    q.last_job = None

    await sm.handle_event("REPRINT", {}, DEFAULT_SETTINGS)

    assert first["type"] == "PRINT_PHOTO"
    assert q.last_job is None


@pytest.mark.anyio
async def test_reprint_is_not_accepted_from_other_screens():
    sm, q, cam, sse = make_sm()
    sm._state.screen = "REVEAL"
    sm._state.printStatus = "failed"
    q.last_job = None

    await sm.handle_event("REPRINT", {}, DEFAULT_SETTINGS)

    assert q.last_job is None
    assert (await sm.get_state()).screen == "REVEAL"


@pytest.mark.anyio
async def test_reprint_reports_the_second_outcome():
    """The retry has to be able to fail again — and to succeed."""
    sm, q = _failed_print()

    await sm.handle_event("REPRINT", {}, DEFAULT_SETTINGS)
    await sm.job_print_failed("Media tray empty.")
    assert (await sm.get_state()).printStatus == "failed"

    await sm.handle_event("REPRINT", {}, DEFAULT_SETTINGS)
    await sm.job_print_done("p1.jpg")
    assert (await sm.get_state()).printStatus == "printed"


@pytest.mark.anyio
async def test_entering_printing_with_nothing_to_print_fails_loudly():
    """Otherwise printStatus sits on 'printing' with no job to ever move it off,
    and the guest watches the printing animation until the session watchdog
    times them out."""
    sm, q, cam, sse = make_sm()
    sm._state.screen = "FRAME_PICKER"
    sm._state.finalPhoto = None

    await sm.handle_event("FRAME_SKIP", {}, FRAMED_SETTINGS)

    state = await sm.get_state()
    assert state.screen == "PRINTING"
    assert state.printStatus == "failed"
    assert q.last_job is None


# ── Print allowance ──────────────────────────────────────────────────────────

class FakeCounters:
    """Stands in for backend.counters.Counters — the FSM only ever reads."""

    def __init__(self, **values):
        self._values = values

    def get(self, name):
        return self._values.get(name, 0)


def _budget(allowance):
    return AppSettings(print_allowance=allowance,
                       overlays=[OverlayConfig(id="none", name="No Frame", filename="")])


def _sm_with_budget(used):
    q, cam, sse = MockQueue(), MockCamera(), FakeSse()
    sm = StateMachine(sse, q, cam, counters=FakeCounters(prints_used=used))
    return sm, q


@pytest.mark.anyio
async def test_print_is_skipped_once_the_allowance_is_spent():
    """Nobody is turned away: the session runs, the photo exists, the QR still
    works. Only the print is dropped."""
    sm, q = _sm_with_budget(used=150)
    sm._state.screen = "FRAME_PICKER"
    sm._state.finalPhoto = "p1.jpg"

    await sm.handle_event("FRAME_SKIP", {}, _budget(150))

    state = await sm.get_state()
    assert state.screen == "PRINTING"
    assert state.printStatus == "skipped"
    assert state.finalPhoto == "p1.jpg"     # the guest still has a photo
    assert q.last_job is None               # nothing was queued


@pytest.mark.anyio
async def test_print_runs_while_the_allowance_holds():
    sm, q = _sm_with_budget(used=149)
    sm._state.screen = "FRAME_PICKER"
    sm._state.finalPhoto = "p1.jpg"

    await sm.handle_event("FRAME_SKIP", {}, _budget(150))

    state = await sm.get_state()
    assert state.printStatus == "printing"
    assert q.last_job["type"] == "PRINT_PHOTO"


@pytest.mark.anyio
async def test_raising_the_allowance_lets_printing_resume():
    sm, q = _sm_with_budget(used=150)
    sm._state.screen = "FRAME_PICKER"
    sm._state.finalPhoto = "p1.jpg"

    await sm.handle_event("FRAME_SKIP", {}, _budget(150))
    assert (await sm.get_state()).printStatus == "skipped"

    sm._state.screen = "FRAME_PICKER"
    await sm.handle_event("FRAME_SKIP", {}, _budget(200))

    assert (await sm.get_state()).printStatus == "printing"
    assert q.last_job is not None


@pytest.mark.anyio
async def test_reprint_is_refused_when_the_print_was_skipped():
    """REPRINT is failure-only, and a spent allowance is not a failure —
    offering a retry that cannot work would be a lie."""
    sm, q, cam, sse = make_sm()
    sm._state.screen = "PRINTING"
    sm._state.finalPhoto = "p1.jpg"
    sm._state.printStatus = "skipped"
    q.last_job = None

    await sm.handle_event("REPRINT", {}, _budget(150))

    assert q.last_job is None
    assert (await sm.get_state()).printStatus == "skipped"

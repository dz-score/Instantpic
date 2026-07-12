import pytest
import asyncio
from backend.state_machine import StateMachine
from backend.config import AppSettings, OverlayConfig

@pytest.fixture
def anyio_backend():
    return 'asyncio'


DEFAULT_SETTINGS = AppSettings()

NO_FRAME_SETTINGS = AppSettings(overlays=[OverlayConfig(id="none", name="No Frame", filename="")])
"""Config with only the 'none' overlay -> print flow skips the frame picker."""

FRAMED_SETTINGS = AppSettings()
"""Default config ships real frames -> print flow routes to the frame picker."""


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
    sm = StateMachine()
    q = MockQueue()
    cam = MockCamera()
    sm.set_job_queue(q)
    sm.set_camera(cam)
    return sm, q, cam


async def fire_and_complete(sm, cam, filename, settings):
    """One full shot: UI fires, camera worker completes via callback."""
    await sm.handle_event("FIRE_SHOT", {}, settings)
    await cam.complete(filename)


@pytest.mark.anyio
async def test_initial_state():
    sm = StateMachine()
    state = await sm.get_state()
    assert state.screen == "ATTRACT"
    assert state.isProcessing is False

@pytest.mark.anyio
async def test_start_session():
    sm = StateMachine()
    await sm.handle_event("START_SESSION", {}, DEFAULT_SETTINGS)
    state = await sm.get_state()
    assert state.screen == "CHOOSE_STYLE"

@pytest.mark.anyio
async def test_layout_sets_shot_count():
    sm = StateMachine()
    await sm.handle_event("START_SESSION", {}, DEFAULT_SETTINGS)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "collage"}, DEFAULT_SETTINGS)
    state = await sm.get_state()
    assert state.screen == "COUNTDOWN"
    assert state.layoutMode == "collage"
    # Shot count is a workflow rule owned by the FSM, not the UI.
    assert state.totalShots == 3

@pytest.mark.anyio
async def test_full_flow_no_frames():
    sm, q, cam = make_sm()

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
    sm, q, cam = make_sm()
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
    sm, q, cam = make_sm()

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
    sm, q, cam = make_sm()

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
    sm, q, cam = make_sm()
    sm._state.screen = "PRINTING"
    sm._state.printStatus = "printing"

    await sm.job_print_done("p1.jpg")
    assert (await sm.get_state()).printStatus == "printed"

    await sm.job_print_failed("printer offline")
    assert (await sm.get_state()).printStatus == "failed"


@pytest.mark.anyio
async def test_frame_skip_enters_printing():
    """FRAME_SKIP prints the already-processed photo through the same path."""
    sm, q, cam = make_sm()
    sm._state.screen = "FRAME_PICKER"
    sm._state.finalPhoto = "p1.jpg"

    await sm.handle_event("FRAME_SKIP", {}, FRAMED_SETTINGS)
    state = await sm.get_state()
    assert state.screen == "PRINTING"
    assert state.printStatus == "printing"
    assert q.last_job["type"] == "PRINT_PHOTO"


@pytest.mark.anyio
async def test_job_failure_recovery():
    sm, q, cam = make_sm()
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
    sm, q, cam = make_sm()
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
    sm, q, cam = make_sm()
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
    sm, q, cam = make_sm()
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
    from backend import state_machine as sm_mod

    sm, q, cam = make_sm()

    payloads = []
    monkeypatch.setattr(sm_mod.sse_svc, "dispatch_event",
                        lambda event, data: payloads.append(data))

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
async def test_stall_watchdog_resets_stranded_countdown(monkeypatch):
    """A session stuck in COUNTDOWN with no shot progress must reset to
    ATTRACT after capture_stall_timeout, and broadcast the reset."""
    from backend import state_machine as sm_mod
    sm, q, cam = make_sm()

    screens = []
    monkeypatch.setattr(sm_mod.sse_svc, "dispatch_event",
                        lambda event, data: screens.append(data["screen"]))

    await sm.handle_event("START_SESSION", {}, STALL_SETTINGS)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"}, STALL_SETTINGS)
    assert (await sm.get_state()).screen == "COUNTDOWN"

    await asyncio.sleep(0.4)

    state = await sm.get_state()
    assert state.screen == "ATTRACT"
    assert screens[-1] == "ATTRACT"  # the reset was broadcast to clients
    # The stall reset is also the floor for a capture whose callback never
    # arrived — the in-flight guard must not leak into the next session.
    assert sm._shot_in_flight is False

@pytest.mark.anyio
async def test_stall_watchdog_rearms_per_shot():
    """Each shot of a multi-shot layout gets a fresh stall window — progress
    mid-sequence must not trip the watchdog, silence after it must."""
    settings = AppSettings(capture_stall_timeout=0.4)
    sm, q, cam = make_sm()

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
async def test_stall_watchdog_cancelled_on_leaving_countdown():
    """Completing the sequence (-> REVEAL) must cancel the watchdog — no
    spurious reset later."""
    sm, q, cam = make_sm()

    await sm.handle_event("START_SESSION", {}, STALL_SETTINGS)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"}, STALL_SETTINGS)
    await fire_and_complete(sm, cam, "raw1.jpg", STALL_SETTINGS)

    assert (await sm.get_state()).screen == "REVEAL"
    assert sm._stall_watchdog is None

    await asyncio.sleep(0.4)  # well past the stall timeout
    assert (await sm.get_state()).screen == "REVEAL"

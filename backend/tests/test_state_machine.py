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
    sm = StateMachine()
    q = MockQueue()
    sm.set_job_queue(q)

    await sm.handle_event("START_SESSION", {}, NO_FRAME_SETTINGS)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "collage"}, NO_FRAME_SETTINGS)

    # The UI reports each shot; the FSM only advances to REVEAL on the last one.
    await sm.handle_event("SHOT_CAPTURED", {"filename": "img1"}, NO_FRAME_SETTINGS)
    state = await sm.get_state()
    assert state.screen == "COUNTDOWN"
    assert state.capturedImages == ["img1"]

    await sm.handle_event("SHOT_CAPTURED", {"filename": "img2"}, NO_FRAME_SETTINGS)
    state = await sm.get_state()
    assert state.screen == "COUNTDOWN"

    await sm.handle_event("SHOT_CAPTURED", {"filename": "img3"}, NO_FRAME_SETTINGS)
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
    sm = StateMachine()
    await sm.handle_event("START_SESSION", {}, NO_FRAME_SETTINGS)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"}, NO_FRAME_SETTINGS)

    await sm.handle_event("SHOT_CAPTURED", {"filename": "img1"}, NO_FRAME_SETTINGS)
    await sm.job_photo_processed("p1.jpg", ["img1"])

    await sm.handle_event("RETAKE", {}, NO_FRAME_SETTINGS)
    state = await sm.get_state()
    assert state.screen == "COUNTDOWN"
    assert state.retakeCount == 1
    assert state.capturedImages == []

    await sm.handle_event("SHOT_CAPTURED", {"filename": "img2"}, NO_FRAME_SETTINGS)
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
    sm = StateMachine()
    q = MockQueue()
    sm.set_job_queue(q)

    await sm.handle_event("START_SESSION", {}, FRAMED_SETTINGS)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"}, FRAMED_SETTINGS)
    await sm.handle_event("SHOT_CAPTURED", {"filename": "img1"}, FRAMED_SETTINGS)
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
async def test_job_failure_recovery():
    sm = StateMachine()
    await sm.handle_event("START_SESSION", {}, DEFAULT_SETTINGS)
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"}, DEFAULT_SETTINGS)
    await sm.handle_event("SHOT_CAPTURED", {"filename": "img1"}, DEFAULT_SETTINGS)

    state = await sm.get_state()
    assert state.isProcessing is True

    await sm.job_failed("Oops")
    state = await sm.get_state()
    assert state.isProcessing is False
    # Still on reveal so they can retake or something
    assert state.screen == "REVEAL"

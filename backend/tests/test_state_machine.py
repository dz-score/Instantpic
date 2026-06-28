import pytest
import asyncio
from backend.state_machine import StateMachine

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.mark.anyio
async def test_initial_state():
    sm = StateMachine()
    state = await sm.get_state()
    assert state.screen == "ATTRACT"
    assert state.isProcessing is False

@pytest.mark.anyio
async def test_start_session():
    sm = StateMachine()
    await sm.handle_event("START_SESSION", {})
    state = await sm.get_state()
    assert state.screen == "CHOOSE_STYLE"

@pytest.mark.anyio
async def test_full_flow_no_frames():
    sm = StateMachine()
    
    await sm.handle_event("START_SESSION", {})
    
    await sm.handle_event("SELECT_LAYOUT", {"mode": "collage"})
    state = await sm.get_state()
    assert state.screen == "COUNTDOWN"
    assert state.layoutMode == "collage"
    
    # Mock job queue
    class MockQueue:
        def __init__(self):
            self.last_job = None
        async def enqueue(self, job):
            self.last_job = job
            
    q = MockQueue()
    sm.set_job_queue(q)
    
    await sm.handle_event("CAPTURE_DONE", {"images": ["img1", "img2"], "text": "foo"})
    state = await sm.get_state()
    assert state.screen == "REVEAL"
    assert state.isProcessing is True
    assert q.last_job["type"] == "PROCESS_PHOTO"
    
    await sm.job_photo_processed("photo.jpg", ["img1", "img2"])
    state = await sm.get_state()
    assert state.isProcessing is False
    assert state.finalPhoto == "photo.jpg"
    assert len(state.allSessionPhotos) == 1
    
    await sm.handle_event("PRINT_FROM_REVEAL", {"overlays": []})
    state = await sm.get_state()
    assert state.screen == "PRINTING"
    
    await sm.handle_event("FINISH", {})
    state = await sm.get_state()
    assert state.screen == "ATTRACT"

@pytest.mark.anyio
async def test_retake_and_pick_favorite():
    sm = StateMachine()
    await sm.handle_event("START_SESSION", {})
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"})
    
    await sm.handle_event("CAPTURE_DONE", {"images": ["img1"]})
    await sm.job_photo_processed("p1.jpg", ["img1"])
    
    await sm.handle_event("RETAKE", {})
    state = await sm.get_state()
    assert state.screen == "COUNTDOWN"
    assert state.retakeCount == 1
    assert state.capturedImages == []
    
    await sm.handle_event("CAPTURE_DONE", {"images": ["img2"]})
    await sm.job_photo_processed("p2.jpg", ["img2"])
    
    # User has multiple photos, printing from reveal goes to pick favorite
    await sm.handle_event("PRINT_FROM_REVEAL", {"overlays": []})
    state = await sm.get_state()
    assert state.screen == "PICK_FAVORITE"
    
    # Pick the first one
    await sm.handle_event("FAVORITE_SELECT", {"filename": "p1.jpg", "overlays": []})
    state = await sm.get_state()
    assert state.screen == "PRINTING"
    assert state.finalPhoto == "p1.jpg"

@pytest.mark.anyio
async def test_frame_picker_flow():
    sm = StateMachine()
    
    # Mock job queue
    class MockQueue:
        def __init__(self):
            self.last_job = None
        async def enqueue(self, job):
            self.last_job = job
            
    q = MockQueue()
    sm.set_job_queue(q)

    await sm.handle_event("START_SESSION", {})
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"})
    await sm.handle_event("CAPTURE_DONE", {"images": ["img1"]})
    await sm.job_photo_processed("p1.jpg", ["img1"])

    # Overlays array with at least one real frame (id != 'none') and length > 1
    overlays = [{"id": "none"}, {"id": "frame1"}]
    
    await sm.handle_event("PRINT_FROM_REVEAL", {"overlays": overlays})
    state = await sm.get_state()
    assert state.screen == "FRAME_PICKER"
    
    await sm.handle_event("FRAME_SELECT", {"overlay_id": "frame1"})
    state = await sm.get_state()
    assert state.isProcessing is True
    assert q.last_job["type"] == "PROCESS_FRAME"
    assert q.last_job["overlay_id"] == "frame1"
    
    await sm.job_frame_processed("framed.jpg")
    state = await sm.get_state()
    assert state.screen == "PRINTING"
    assert state.finalPhoto == "framed.jpg"

@pytest.mark.anyio
async def test_job_failure_recovery():
    sm = StateMachine()
    await sm.handle_event("START_SESSION", {})
    await sm.handle_event("SELECT_LAYOUT", {"mode": "single"})
    await sm.handle_event("CAPTURE_DONE", {"images": ["img1"]})
    
    state = await sm.get_state()
    assert state.isProcessing is True
    
    await sm.job_failed("Oops")
    state = await sm.get_state()
    assert state.isProcessing is False
    # Still on reveal so they can retake or something
    assert state.screen == "REVEAL"

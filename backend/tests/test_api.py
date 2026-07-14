def test_get_config(client):
    """Test retrieving configuration."""
    response = client.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert "countdown_duration" in data
    assert "admin_pin" in data

def test_post_config(client):
    """Test updating configuration."""
    response = client.post("/api/config", json={"countdown_duration": 5})
    assert response.status_code == 200
    data = response.json()
    assert data["countdown_duration"] == 5

def test_event_capture_flow(client):
    """FIRE_SHOT over HTTP reaches the FSM and enqueues a capture on the
    injected camera. Completion is backend-owned (camera callback -> FSM,
    unit-tested in test_state_machine) — the HTTP layer only needs to prove
    the trigger seam; the browser never reports the shot."""
    from backend.state_machine import state_machine

    class _Cam:
        def __init__(self):
            self.jobs = 0
        def enqueue_capture(self, on_complete=None, on_failure=None):
            self.jobs += 1
            return "jobT"

    cam = _Cam()
    state_machine.set_camera(cam)
    try:
        client.post("/api/events", json={"type": "TIMEOUT", "payload": {}})  # known state
        client.post("/api/events", json={"type": "START_SESSION", "payload": {}})
        client.post("/api/events", json={"type": "SELECT_LAYOUT", "payload": {"mode": "single"}})

        response = client.post("/api/events", json={"type": "FIRE_SHOT", "payload": {}})
        assert response.status_code == 200
        assert cam.jobs == 1

        # Still COUNTDOWN — the shot lands via the camera callback, not HTTP.
        assert client.get("/api/state").json()["screen"] == "COUNTDOWN"
    finally:
        state_machine.set_camera(None)
        state_machine._shot_in_flight = False
        client.post("/api/events", json={"type": "TIMEOUT", "payload": {}})  # reset shared FSM

def test_change_pin_success(client):
    """Test changing the admin PIN with correct current PIN."""
    response = client.post("/api/change-pin", json={
        "current_pin": "123456",
        "new_pin": "987654"
    })
    assert response.status_code == 200
    
    # Verify it updated in config
    config_resp = client.get("/api/config")
    assert config_resp.json()["admin_pin"] == "987654"

def test_change_pin_invalid(client):
    """Test that providing the wrong current PIN blocks the change."""
    response = client.post("/api/change-pin", json={
        "current_pin": "000000",
        "new_pin": "111111"
    })
    assert response.status_code == 403
    assert "Invalid" in response.json()["detail"]

def test_diagnostics(client):
    """Test retrieving system diagnostics."""
    response = client.get("/api/diagnostics")
    assert response.status_code == 200
    data = response.json()
    assert "printer" in data
    assert "storage" in data

def test_camera_capture(client, mocker):
    """Test enqueuing a camera capture."""
    mock_svc = mocker.patch("backend.camera_provider.camera_svc")
    mock_svc.enqueue_capture.return_value = "1234abcd"

    response = client.post("/api/camera/capture")
    assert response.status_code == 200
    assert response.json()["status"] == "enqueued"
    assert response.json()["job_id"] == "1234abcd"


def test_camera_route_501_without_backend(client, mocker):
    """With no camera backend, the camera routes 501 rather than crashing."""
    mocker.patch("backend.camera_provider.camera_svc", None)

    assert client.post("/api/camera/capture").status_code == 501
    # ...but status still answers, reporting the camera as disconnected.
    resp = client.get("/api/camera/status")
    assert resp.status_code == 200
    assert resp.json()["connected"] is False


def test_lifespan_startup_and_shutdown(temp_workspace, temp_config):
    """Run the real lifespan (TestClient as context manager). The plain
    TestClient(app) fixture never executes it, which let an
    UnboundLocalError in startup (shadowed sse_svc import) reach runtime
    unseen — this would have caught it."""
    from fastapi.testclient import TestClient
    from backend.main import app

    with TestClient(app) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        # bind_loop() ran: the SSE service holds the app's event loop.
        from backend.sse_service import sse_svc
        assert sse_svc._loop is not None

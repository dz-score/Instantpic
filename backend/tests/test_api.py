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

def test_led_config_defaults_are_served(client):
    """The ring is off by default and the block is nested under a transport key."""
    led = client.get("/api/config").json()["led"]
    assert led["enabled"] is False
    assert led["transport"] == "http"
    assert led["http"]["host"] == ""

def test_partial_led_update_does_not_reset_the_rest_of_the_block(client):
    """SettingsService.update() replaces top-level keys outright. Without the
    merge, setting the host alone would switch the ring off."""
    client.post("/api/config", json={"led": {"enabled": True, "http": {"host": "10.0.0.9"}}})

    r = client.post("/api/config", json={"led": {"http": {"host": "10.0.0.42"}}})
    assert r.status_code == 200
    led = r.json()["led"]
    assert led["http"]["host"] == "10.0.0.42"
    assert led["enabled"] is True                     # not reset to the default
    assert led["http"]["capture_timeout_ms"] == 250   # sibling under http survives

def test_partial_printer_mock_update_keeps_the_rest_of_the_block(client):
    """Same merge, second block: an operator changing one mock fault must not
    silently take the simulated print time back to its default."""
    client.post("/api/config", json={"printer_mock": {"job_duration_s": 4.0, "media_total": 20}})

    r = client.post("/api/config", json={"printer_mock": {"fault": "abort_mid_job"}})
    assert r.status_code == 200
    mock = r.json()["printer_mock"]
    assert mock["fault"] == "abort_mid_job"
    assert mock["job_duration_s"] == 4.0
    assert mock["media_total"] == 20


def test_led_update_leaves_unrelated_settings_alone(client):
    client.post("/api/config", json={"countdown_duration": 7})
    r = client.post("/api/config", json={"led": {"enabled": True}})
    assert r.json()["countdown_duration"] == 7

def test_led_config_change_is_applied_without_a_restart(client):
    """Whoever is setting the booth up is standing at the venue typing an IP;
    the useful feedback is the status dot going green while they watch."""
    class _Led:
        def __init__(self):
            self.applied = []
        async def reconfigure(self, settings):
            self.applied.append(settings.led.http.host)
            return True

    led = _Led()
    client.app.state.led = led

    client.post("/api/config", json={"led": {"enabled": True, "http": {"host": "10.0.0.42"}}})
    assert led.applied == ["10.0.0.42"]

    # A save that does not mention the ring must not touch it at all.
    client.post("/api/config", json={"countdown_duration": 4})
    assert led.applied == ["10.0.0.42"]

def test_bad_led_host_does_not_fail_the_config_save(client):
    """Otherwise the operator cannot correct the value they just typed."""
    class _Led:
        async def reconfigure(self, settings):
            raise OSError("no route to host")

    client.app.state.led = _Led()

    r = client.post("/api/config", json={"led": {"http": {"host": "nonsense"}}})
    assert r.status_code == 200
    assert r.json()["led"]["http"]["host"] == "nonsense"

def test_event_capture_flow(client):
    """FIRE_SHOT over HTTP reaches the FSM and enqueues a capture on the
    injected camera. Completion is backend-owned (camera callback -> FSM,
    unit-tested in test_state_machine) — the HTTP layer only needs to prove
    the trigger seam; the browser never reports the shot."""
    class _Cam:
        def __init__(self):
            self.jobs = 0
        def enqueue_capture(self, on_complete=None, on_failure=None):
            self.jobs += 1
            return "jobT"

    cam = _Cam()
    # The lifespan builds a fresh FSM per app, so this test owns its own instance —
    # no restoring a shared singleton afterwards, and no TIMEOUT to reset it first.
    client.app.state.state_machine._camera = cam

    client.post("/api/events", json={"type": "START_SESSION", "payload": {}})
    client.post("/api/events", json={"type": "SELECT_LAYOUT", "payload": {"mode": "single"}})

    response = client.post("/api/events", json={"type": "FIRE_SHOT", "payload": {}})
    assert response.status_code == 200
    assert cam.jobs == 1

    # Still COUNTDOWN — the shot lands via the camera callback, not HTTP.
    assert client.get("/api/state").json()["screen"] == "COUNTDOWN"

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

def test_diagnostics_reports_the_ring(client):
    """The latency percentiles here are the evidence the HTTP-vs-UART decision
    rests on (Docs/LED_UART_SWITCH.md). Before this they were collected and
    never read by anything."""
    led = client.get("/api/diagnostics").json()["led"]
    assert led["enabled"] is False      # no ring configured in a test config
    assert led["connected"] is False
    assert "counts" in led

def test_led_test_route_pings_the_node(client):
    class _Led:
        async def ping(self):
            return {"ok": True, "reply": "PONG", "elapsed_ms": 12.3, "detail": None}

    client.app.state.led = _Led()
    r = client.post("/api/led/test")
    assert r.status_code == 200
    assert r.json()["reply"] == "PONG"

def test_led_test_route_is_refused_mid_session(client):
    """It injects a command into the same single-owner queue the shutter waits
    on, so it must not be tappable while a guest is being photographed."""
    class _Led:
        def __init__(self):
            self.pings = 0
        async def ping(self):
            self.pings += 1
            return {"ok": True, "reply": "PONG", "elapsed_ms": 1.0, "detail": None}

    led = _Led()
    client.app.state.led = led
    client.post("/api/events", json={"type": "START_SESSION", "payload": {}})

    r = client.post("/api/led/test")
    assert r.status_code == 409
    assert led.pings == 0

def test_led_channel_route_lights_one_die(client):
    class _Led:
        enabled = True
        def __init__(self):
            self.sent = []
        async def test_channel(self, ch):
            self.sent.append(ch)
            return "OK TEST"
        async def idle(self):
            self.sent.append("idle")
        async def drain(self, timeout_s=2.0):
            return True

    led = _Led()
    client.app.state.led = led

    for name, arg in [("red", 1), ("green", 2), ("blue", 3), ("white", 4)]:
        r = client.post("/api/led/channel", json={"channel": name})
        assert r.status_code == 200, name
        assert r.json()["ok"] is True

    assert led.sent == [1, 2, 3, 4]

    client.post("/api/led/channel", json={"channel": "off"})
    assert led.sent[-1] == "idle"

def test_led_channel_route_rejects_an_unknown_colour(client):
    class _Led:
        enabled = True
        async def test_channel(self, ch):
            raise AssertionError("should not be reached")

    client.app.state.led = _Led()
    r = client.post("/api/led/channel", json={"channel": "puce"})
    assert r.status_code == 400

def test_led_channel_route_is_refused_mid_session(client):
    """Full white for two minutes is not something the admin panel should be
    able to start underneath a guest."""
    class _Led:
        enabled = True
        def __init__(self):
            self.calls = 0
        async def test_channel(self, ch):
            self.calls += 1
            return "OK TEST"

    led = _Led()
    client.app.state.led = led
    client.post("/api/events", json={"type": "START_SESSION", "payload": {}})

    r = client.post("/api/led/channel", json={"channel": "red"})
    assert r.status_code == 409
    assert led.calls == 0

def test_camera_route_uses_injected_service(client, mocker):
    """The camera is whatever the composition root put on app.state, so a double
    goes in the same way — no monkeypatching a module global to reach the
    service. (There is deliberately no POST /capture route: the shutter fires
    only through the FSM's FIRE_SHOT, which owns the in-flight guard.)"""
    mock_svc = mocker.MagicMock()
    client.app.state.camera = mock_svc

    response = client.post("/api/camera/resume")
    assert response.status_code == 200
    mock_svc.resume_preview.assert_called_once()


def test_camera_route_501_without_backend(client):
    """With no camera backend, the camera routes 501 rather than crashing."""
    client.app.state.camera = None

    assert client.post("/api/camera/resume").status_code == 501
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
        # The composition root ran: every service the routes depend on is on app.state.
        assert app.state.settings.get() is not None
        assert app.state.print_svc is not None
        assert app.state.state_machine is not None
        # bind_loop() ran: the SSE service holds the app's event loop.
        assert app.state.sse._loop is not None


def test_printer_options_are_reachable_from_the_admin_panel(client):
    """They were in AppSettings but missing from ConfigUpdateRequest, so the only
    way to change them was hand-editing config.json plus a restart — no good when
    the person who needs to try another option string is standing at the printer."""
    r = client.post("/api/config", json={"printer_options": "media=w288h432 scaling=100"})
    assert r.status_code == 200
    assert r.json()["printer_options"] == "media=w288h432 scaling=100"


def test_diagnostics_carry_the_driver_and_media(client):
    """The operator has to be able to tell a real printer from a simulated one —
    on Windows the mock is chosen whatever the queue name says."""
    printer = client.get("/api/diagnostics").json()["printer"]
    assert printer["driver"] == "mock"
    assert printer["prints_remaining"] is not None
    assert printer["status"] == printer["status_text"]   # alias the UI reads


def test_test_print_reports_the_real_outcome(client):
    client.post("/api/config", json={"printer_mock": {"job_duration_s": 0}})

    body = client.post("/api/printer/test").json()

    assert body["ok"] is True
    assert "printtest" in body["filename"]


def test_test_print_surfaces_a_jam(client):
    """The mock jams after acceptance. The operator must be told that, not told
    the card printed."""
    client.post("/api/config", json={
        "printer_mock": {"job_duration_s": 0, "fault": "abort_mid_job"},
    })

    body = client.post("/api/printer/test").json()

    assert body["ok"] is False
    assert "Paper jam." in body["detail"]


def test_test_print_is_refused_mid_session(client):
    """It queues onto the same serial print lane a guest's photo uses, so nobody
    can push a diagnostic in front of a print someone is waiting on. Same rule
    the LED tests follow."""
    client.post("/api/events", json={"type": "START_SESSION", "payload": {}})

    r = client.post("/api/printer/test")

    assert r.status_code == 409
    assert "busy" in r.json()["detail"].lower()

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

def test_event_capture_done(client, mock_base64_image):
    """Test sending a valid capture done event."""
    payload = {
        "type": "CAPTURE_DONE",
        "payload": {
            "images": [mock_base64_image],
            "text": "API Integration Test",
            "overlay_id": "none"
        }
    }
    response = client.post("/api/events", json=payload)
    assert response.status_code == 200
    
    # State should now be processing/reveal
    state_resp = client.get("/api/state")
    assert state_resp.status_code == 200
    assert state_resp.json()["screen"] == "REVEAL"

def test_print_photo_missing(client):
    """Test that requesting to print a missing file returns 404."""
    response = client.post("/api/print/i_do_not_exist.jpg")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()

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

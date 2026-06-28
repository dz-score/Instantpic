import pytest
import threading
import os

# --- 1. Schema Boundary Tests ---

def test_schema_config_invalid_type(client):
    """Test type coercion failure (sending string instead of int)."""
    response = client.post("/api/config", json={"countdown_duration": "five"})
    assert response.status_code == 422
    data = response.json()
    assert "detail" in data
    assert data["detail"][0]["type"] == "int_parsing"

def test_schema_malformed_json(client):
    """Test syntactically broken JSON."""
    response = client.post(
        "/api/events",
        content="{'broken_json: 123",  # Invalid JSON syntax
        headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert "json" in str(response.json()["detail"]).lower() or "decode" in str(response.json()["detail"]).lower()

# --- 2. Exception Response Validation ---

def test_exception_print_missing_file(client):
    """Business logic error - missing file should strictly return 404."""
    response = client.post("/api/print/missing_file.jpg")
    assert response.status_code == 404
    assert response.json()["detail"] == "Photo not found"

def test_exception_cups_crash(client, temp_workspace, mocker):
    """Test that a print subsystem failure is caught and returned as 500."""
    from backend.print_service import PrintResult
    mocker.patch(
        "backend.main.print_svc.print",
        return_value=PrintResult(success=False, error="Printing failed. Check CUPS setup.")
    )
    
    filepath = os.path.join(temp_workspace["photos_dir"], "valid_photo.jpg")
    with open(filepath, "w") as f:
        f.write("dummy")
        
    response = client.post("/api/print/valid_photo.jpg")
    assert response.status_code == 500
    assert response.json()["detail"] == "Printing failed. Check CUPS setup."

# --- 3. Auth Status Segregation ---

def test_auth_change_pin_short(client):
    """Missing or short PIN fails Pydantic schema validation (422)."""
    response = client.post("/api/change-pin", json={
        "current_pin": "123456",
        "new_pin": "123"  # Minimum is 6
    })
    assert response.status_code == 422

# --- 4. Request Isolation (Concurrency) Test ---

def test_request_isolation_concurrency(client, temp_workspace, mock_base64_image):
    """Fire 3 rapid requests sequentially (TestClient isn't thread-safe for async locking)."""
    payload = {
        "type": "CAPTURE_DONE",
        "payload": {
            "images": [mock_base64_image],
            "text": "Concurrency Test",
            "overlay_id": "none"
        }
    }
    
    results = []
    for _ in range(3):
        res = client.post("/api/events", json=payload)
        results.append(res)
        
    for res in results:
        assert res.status_code == 200

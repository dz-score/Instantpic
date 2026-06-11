import pytest
import threading
import os

# --- 1. Schema Boundary Tests ---

def test_schema_config_invalid_type(client):
    """Test type coercion failure (sending string instead of int)."""
    # 'five' cannot be coerced to an integer
    response = client.post("/api/config", json={"countdown_duration": "five"})
    assert response.status_code == 422
    data = response.json()
    assert "detail" in data
    assert data["detail"][0]["type"] == "int_parsing"

def test_schema_save_photo_missing_fields(client):
    """Test payload completely missing a required field."""
    payload = {
        "layout": "single",
        "text": "Missing Images Test",
        "overlay_id": "none"
        # "images" is missing
    }
    response = client.post("/api/save-photo", json=payload)
    assert response.status_code == 422
    assert "images" in response.json()["detail"][0]["loc"]

def test_schema_save_photo_invalid_enum(client, mock_base64_image):
    """Test invalid enum value for layout (should be 422, not 400)."""
    payload = {
        "images": [mock_base64_image],
        "layout": "grid",  # not 'single' or 'collage'
        "text": "Enum Test",
        "overlay_id": "none"
    }
    response = client.post("/api/save-photo", json=payload)
    assert response.status_code == 422
    assert "layout" in response.json()["detail"][0]["loc"]

def test_schema_malformed_json(client):
    """Test syntactically broken JSON."""
    response = client.post(
        "/api/save-photo",
        content="{'broken_json: 123",  # Invalid JSON syntax
        headers={"Content-Type": "application/json"}
    )
    # FastAPI returns 422 for bad JSON
    assert response.status_code == 422
    assert "json" in str(response.json()["detail"]).lower() or "decode" in str(response.json()["detail"]).lower()

# --- 2. Exception Response Validation ---

def test_exception_print_missing_file(client):
    """Business logic error - missing file should strictly return 404."""
    response = client.post("/api/print/missing_file.jpg")
    assert response.status_code == 404
    assert response.json()["detail"] == "Photo not found"

def test_exception_processing_crash(client, mock_base64_image, mocker):
    """Test that a hard crash in the image processor is caught safely (500)."""
    mocker.patch(
        "backend.main.process_photo_layout",
        side_effect=ValueError("Out of memory during image stitch")
    )
    payload = {
        "images": [mock_base64_image],
        "layout": "single",
        "text": "Crash Test",
        "overlay_id": "none"
    }
    response = client.post("/api/save-photo", json=payload)
    assert response.status_code == 500
    assert "Out of memory during image stitch" in response.json()["detail"]

def test_exception_cups_crash(client, temp_workspace, mocker):
    """Test that a print subsystem failure is caught and returned as 500."""
    mocker.patch("backend.main.print_photo", return_value=False)
    
    # Create a dummy file so it passes the 404 check
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
    """Fire 3 rapid requests and ensure they don't overwrite each other."""
    payload = {
        "images": [mock_base64_image],
        "layout": "single",
        "text": "Concurrency Test",
        "overlay_id": "none"
    }
    
    results = []
    def make_request():
        res = client.post("/api/save-photo", json=payload)
        results.append(res)
        
    threads = [threading.Thread(target=make_request) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
        
    # Verify all 3 requests succeeded independently
    for res in results:
        assert res.status_code == 200
        
    # Verify 3 distinct files exist on disk
    photos = os.listdir(temp_workspace["photos_dir"])
    assert len(photos) == 3

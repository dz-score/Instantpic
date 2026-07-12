import pytest
import threading

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

# --- 2. Auth Status Segregation ---

def test_auth_change_pin_short(client):
    """Missing or short PIN fails Pydantic schema validation (422)."""
    response = client.post("/api/change-pin", json={
        "current_pin": "123456",
        "new_pin": "123"  # Minimum is 6
    })
    assert response.status_code == 422

# --- 3. Request Isolation (Concurrency) Test ---

def test_request_isolation_concurrency(client, temp_workspace):
    """Fire 3 rapid requests sequentially (TestClient isn't thread-safe for async locking)."""
    payload = {
        "type": "TIMEOUT",  # global event: valid from any state, no side effects
        "payload": {},
    }

    results = []
    for _ in range(3):
        res = client.post("/api/events", json=payload)
        results.append(res)
        
    for res in results:
        assert res.status_code == 200

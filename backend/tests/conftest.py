import os
import json
import tempfile

# Route the structured logger's files into a temp dir so test runs don't
# pollute the real logs/ directory. backend.logger creates its log files at
# import time, so this must be set before any backend module is imported —
# pytest imports conftest.py before collecting the test modules, making this
# the one place that reliably runs first.
os.environ.setdefault("BOOTH_LOG_DIR", tempfile.mkdtemp(prefix="booth-test-logs-"))

import pytest
import base64
from io import BytesIO
from PIL import Image

@pytest.fixture
def temp_workspace(tmp_path, monkeypatch):
    """
    Creates an isolated temporary directory for photos and overlays,
    and patches the backend to use these directories instead of the real ones.
    """
    photos_dir = tmp_path / "photos"
    overlays_dir = tmp_path / "overlays"
    photos_dir.mkdir()
    overlays_dir.mkdir()
    
    # Patch storage module
    import backend.storage as storage
    monkeypatch.setattr(storage, "PHOTOS_DIR", str(photos_dir))
    monkeypatch.setattr(storage, "OVERLAYS_DIR", str(overlays_dir))
    
    # Patch photo_processor module
    import backend.photo_processor as photo_processor
    monkeypatch.setattr(photo_processor, "PHOTOS_DIR", str(photos_dir))
    monkeypatch.setattr(photo_processor, "OVERLAYS_DIR", str(overlays_dir))
    
    # Patch main module
    import backend.main as main_mod
    monkeypatch.setattr(main_mod, "PHOTOS_DIR", str(photos_dir))
    monkeypatch.setattr(main_mod, "OVERLAYS_DIR", str(overlays_dir))
    
    return {
        "photos_dir": str(photos_dir),
        "overlays_dir": str(overlays_dir)
    }

@pytest.fixture
def temp_config(tmp_path, monkeypatch):
    """
    Creates an isolated temporary config file and patches the config module
    to prevent overwriting the real config.json.
    """
    config_file = tmp_path / "config.json"
    # Write empty config to simulate fresh install or missing values
    config_file.write_text("{}")
    
    import backend.config as config
    monkeypatch.setattr(config, "CONFIG_PATH", str(config_file))
    
    return str(config_file)

@pytest.fixture
def mock_base64_image():
    """Returns a simple 100x100 white square encoded as a base64 string."""
    img = Image.new('RGB', (100, 100), color='white')
    buffered = BytesIO()
    img.save(buffered, format="JPEG")
    img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{img_str}"

@pytest.fixture
def client(temp_workspace, temp_config):
    """Provides a TestClient connected to the FastAPI app with isolated storage."""
    from fastapi.testclient import TestClient
    from backend.main import app
    return TestClient(app)

@pytest.fixture
def mock_gphoto2(monkeypatch):
    """
    Mocks the gphoto2 library to allow testing CameraService without physical hardware.
    Returns the mocked gphoto2 module.
    """
    from unittest.mock import MagicMock
    import sys
    
    mock_gp = MagicMock()
    
    # Setup some basic expected gphoto2 constants and types
    mock_gp.GP_CAPTURE_IMAGE = 0
    mock_gp.GP_WIDGET_RADIO = 1
    
    class GPhoto2Error(Exception):
        pass
    mock_gp.GPhoto2Error = GPhoto2Error
    
    # Create mock Context and Camera
    mock_context = MagicMock()
    mock_camera = MagicMock()
    
    # Helper to simulate capturing a preview returning valid mock file
    mock_preview_file = MagicMock()
    mock_preview_file.get_data_and_size.return_value = (b'fake_jpeg_data', 14)
    mock_camera.capture_preview.return_value = mock_preview_file
    
    mock_gp.Context = MagicMock(return_value=mock_context)
    mock_gp.Camera = MagicMock(return_value=mock_camera)
    
    # Patch into sys.modules
    monkeypatch.setitem(sys.modules, 'gphoto2', mock_gp)
    
    return mock_gp

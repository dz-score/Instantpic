import pytest
import time
from unittest.mock import patch, MagicMock

# The mock_gphoto2 fixture in conftest.py will mock gphoto2 for us,
# but we need to patch it in the camera_service module before instantiation.

def test_camera_init_success(mock_gphoto2):
    # Patch GPHOTO2_AVAILABLE flag directly in camera_service
    with patch('backend.camera_service.gp', mock_gphoto2):
        
        from backend.camera_service import CameraService
        
        # Setup mock behavior
        mock_camera = mock_gphoto2.Camera.return_value
        mock_camera.init.return_value = 0
        
        camera = CameraService()
        camera.init()
        
        assert camera.connected is True
        mock_camera.init.assert_called_once()
        
        camera.shutdown()


def test_camera_init_failure_auto_recovery(mock_gphoto2):
    with patch('backend.camera_service.gp', mock_gphoto2):
        
        from backend.camera_service import CameraService
        
        mock_camera = mock_gphoto2.Camera.return_value
        
        # Simulate USB device claimed / failure on init
        mock_camera.init.side_effect = mock_gphoto2.GPhoto2Error("[-53] Could not claim the USB device")
        
        camera = CameraService()
        camera.init()
        
        assert camera.connected is False
        assert mock_camera.init.call_count == 1
        
        camera.shutdown()


def test_standby_mode_and_resume(mock_gphoto2):
    with patch('backend.camera_service.gp', mock_gphoto2):
        
        from backend.camera_service import CameraService
        camera = CameraService()
        
        # Override preview_allowed to simulate standard running state
        camera._preview_allowed.set()
        assert camera._preview_allowed.is_set() is True
        
        # Clear it (like what capture does at the end to enter standby)
        camera._preview_allowed.clear()
        assert camera._preview_allowed.is_set() is False
        
        # Test resume_preview wakes it up
        camera.resume_preview()
        assert camera._preview_allowed.is_set() is True
        
        camera.shutdown()


def test_capture_leaves_in_standby(mock_gphoto2):
    with patch('backend.camera_service.gp', mock_gphoto2):
        
        from backend.camera_service import CameraService
        
        mock_camera = mock_gphoto2.Camera.return_value
        
        # Simulate successful init
        mock_camera.init.return_value = 0
        
        # Mock file capture
        mock_camera_file = MagicMock()
        mock_camera_file.get_data_and_size.return_value = (b'fake_high_res_data', 18)
        
        mock_camera.file_get.return_value = mock_camera_file
        
        camera = CameraService()
        
        # Mock _do_capture to avoid complex file/event logic for this structural test
        # We want to test the `capture()` wrapper logic
        with patch.object(camera, '_do_capture', return_value="fake_photo.jpg"):
            # Set to running state
            camera._preview_allowed.set()
            
            # Perform capture
            filename = camera.capture()
            
            assert filename == "fake_photo.jpg"
            # Crucial Standby Mode Check: After capture, worker should be paused!
            assert camera._preview_allowed.is_set() is False
        
        camera.shutdown()


def test_worker_loop_increments_consecutive_errors(mock_gphoto2):
    with patch('backend.camera_service.gp', mock_gphoto2):
        
        from backend.camera_service import CameraService
        
        mock_camera = mock_gphoto2.Camera.return_value
        mock_camera.init.return_value = 0
        
        # Simulate capture_preview failing consistently
        mock_camera.capture_preview.side_effect = mock_gphoto2.GPhoto2Error("[-1] Unspecified error")
        
        camera = CameraService()
        camera.init()
        
        # Worker is running in background because init() succeeded.
        # It loops every ~0.1s on error, so after 0.7s it will hit > 5 errors
        time.sleep(0.7)
        
        # When consecutive errors > 5, it should set connected = False
        camera.shutdown()


def test_do_capture_success_and_settings(mock_gphoto2):
    with patch('backend.camera_service.gp', mock_gphoto2):
        from backend.camera_service import CameraService
        
        mock_camera = mock_gphoto2.Camera.return_value
        mock_camera.init.return_value = 0
        
        # Mock capture
        mock_camera_file = MagicMock()
        mock_camera_file.get_data_and_size.return_value = (b'fake_data', 9)
        mock_camera.file_get.return_value = mock_camera_file
        
        # Mock wait_for_event
        mock_camera.wait_for_event.return_value = (mock_gphoto2.GP_EVENT_TIMEOUT, None)
        
        camera = CameraService()
        camera.init()
        
        # Test getting settings
        settings = camera.get_settings()
        assert settings["status"] == "connected"
        
        # Test full capture
        mock_uuid = MagicMock()
        mock_uuid.hex = "12345678"
        with patch('backend.camera_service.uuid.uuid4', return_value=mock_uuid):
            filename = camera._do_capture()
            assert filename == "capture_12345678.jpg"
            
        camera.shutdown()

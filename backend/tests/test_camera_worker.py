import pytest
from unittest.mock import patch, MagicMock

def test_worker_flushes_events(mock_gphoto2):
    """
    Test that the background worker continuously calls wait_for_event
    to prevent camera buffer overflow during live view.
    """
    with patch('backend.camera_service.gp', mock_gphoto2):
        # Ensure timeout constant is mocked
        mock_gphoto2.GP_EVENT_TIMEOUT = 2
        mock_gphoto2.GP_EVENT_UNKNOWN = 3
        
        mock_camera = mock_gphoto2.Camera.return_value
        
        # Simulate wait_for_event returning one event, then a timeout.
        # This tests the while loop logic inside the event flush block.
        mock_camera.wait_for_event.side_effect = [
            (mock_gphoto2.GP_EVENT_UNKNOWN, None),
            (mock_gphoto2.GP_EVENT_TIMEOUT, None)
        ]
        
        from backend.camera_service import CameraService
        camera = CameraService()
        camera.connected = True
        camera.camera = mock_camera
        
        # Run exactly one iteration of the worker loop (is_set is called twice per loop)
        camera._shutdown_event.is_set = MagicMock(side_effect=[False, False, True])
        camera._preview_allowed.set()
        
        # Patch sleep to avoid slowing down tests
        with patch('time.sleep'):
            camera._camera_worker()
            
        # Verify wait_for_event was called to flush the queue
        assert mock_camera.wait_for_event.call_count == 2
        
        # Verify capture_preview was called AFTER the flush
        mock_camera.capture_preview.assert_called_once()


def test_capture_job_flushes_and_avoids_preview(mock_gphoto2):
    """
    Test that _execute_capture_job explicitly flushes leftover events
    from the standby period, and NEVER calls capture_preview() which
    would cause a 3-second delay on hardware.
    """
    with patch('backend.camera_service.gp', mock_gphoto2):
        mock_gphoto2.GP_EVENT_TIMEOUT = 2
        mock_gphoto2.GP_CAPTURE_IMAGE = 1
        
        mock_camera = mock_gphoto2.Camera.return_value
        mock_camera.wait_for_event.side_effect = [
            (mock_gphoto2.GP_EVENT_TIMEOUT, None),
            (mock_gphoto2.GP_EVENT_TIMEOUT, None)
        ]
        
        from backend.camera_service import CameraService
        camera = CameraService()
        camera.connected = True
        camera.camera = mock_camera
        
        # Mock dependencies
        camera._job_queue = MagicMock()
        
        # Execute the capture logic
        camera._execute_capture_job("job_123")
        
        # Ensure pre-capture and post-capture flushes happened
        assert mock_camera.wait_for_event.call_count >= 1
        
        # Ensure high-res capture was triggered
        mock_camera.capture.assert_called_once_with(mock_gphoto2.GP_CAPTURE_IMAGE)
        
        # CRITICAL: Ensure capture_preview was NOT called. 
        # Calling it here breaks hardware stability.
        mock_camera.capture_preview.assert_not_called()


def test_standby_clears_preview_allowed(mock_gphoto2):
    """
    Test that standby mode clears the _preview_allowed flag,
    effectively pausing the worker loop and stopping USB traffic.
    """
    with patch('backend.camera_service.gp', mock_gphoto2):
        from backend.camera_service import CameraService
        camera = CameraService()
        
        camera.resume_preview()
        assert camera._preview_allowed.is_set() is True
        
        camera.standby()
        assert camera._preview_allowed.is_set() is False

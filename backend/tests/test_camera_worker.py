import time
import pytest
from unittest.mock import patch, MagicMock

def test_worker_does_not_flush_events_per_frame(mock_gphoto2):
    """
    The preview loop grabs the frame and nothing else. A per-frame wait_for_event
    drain costs 12-30ms of every ~66ms frame and prevents nothing — don't re-add it.
    Events are drained around the capture instead, where they matter (see
    test_capture_job_flushes_and_avoids_preview: that's what clears FILE_ADDED).
    """
    with patch('backend.camera_service.gp', mock_gphoto2):
        mock_gphoto2.GP_EVENT_TIMEOUT = 2
        mock_camera = mock_gphoto2.Camera.return_value
        mock_camera.wait_for_event.return_value = (mock_gphoto2.GP_EVENT_TIMEOUT, None)

        from backend.camera_service import CameraService
        camera = CameraService(MagicMock())
        camera.connected = True
        camera.camera = mock_camera

        # Run exactly one iteration of the worker loop (is_set is called twice per loop)
        camera._shutdown_event.is_set = MagicMock(side_effect=[False, False, True])
        camera._preview_allowed.set()

        with patch('time.sleep'):
            camera._camera_worker()

        mock_camera.capture_preview.assert_called_once()
        assert mock_camera.wait_for_event.call_count == 0, \
            "the preview loop must not flush events per frame — it costs 12-30ms/frame " \
            "and prevents nothing"


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
        camera = CameraService(MagicMock())
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


def test_preview_failure_disconnects_after_six_errors(mock_gphoto2):
    """
    Six consecutive preview failures = a real disconnect (camera unplugged, USB
    dropped), and the worker must mark itself disconnected so init()'s backoff can
    take over. Fewer than six must NOT: a transient blip is not an outage, and a
    hair trigger here makes a reconnected session re-trip on its first stumble.
    """
    with patch('backend.camera_service.gp', mock_gphoto2):
        mock_gphoto2.GP_EVENT_TIMEOUT = 2
        mock_camera = mock_gphoto2.Camera.return_value
        mock_camera.wait_for_event.return_value = (mock_gphoto2.GP_EVENT_TIMEOUT, None)
        mock_camera.capture_preview.side_effect = Exception("[-1] Unspecified error")

        from backend.camera_service import CameraService
        camera = CameraService(MagicMock())
        camera.connected = True
        camera.camera = mock_camera
        camera._preview_allowed.set()
        camera._last_preview_request = time.monotonic()      # viewer is attached
        camera._last_init_time = time.monotonic() - 10       # warmup grace elapsed

        # The worker checks _shutdown_event twice per iteration (loop top, then
        # again after the _preview_allowed wait), so 12 Falses = 6 full attempts,
        # and the 13th call exits the loop before it can re-init.
        camera._shutdown_event.is_set = MagicMock(
            side_effect=[False] * 12 + [True])

        with patch('time.sleep'):
            camera._camera_worker()

        assert mock_camera.capture_preview.call_count == 6
        assert camera.connected is False


def test_standby_clears_preview_allowed(mock_gphoto2):
    """
    Test that standby mode clears the _preview_allowed flag,
    effectively pausing the worker loop and stopping USB traffic.
    """
    with patch('backend.camera_service.gp', mock_gphoto2):
        from backend.camera_service import CameraService
        camera = CameraService(MagicMock())
        
        camera.resume_preview()
        assert camera._preview_allowed.is_set() is True
        
        camera.standby()
        assert camera._preview_allowed.is_set() is False

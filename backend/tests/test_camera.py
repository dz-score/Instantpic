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
        
        camera = CameraService(MagicMock())
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
        
        camera = CameraService(MagicMock())
        camera.init()
        
        assert camera.connected is False
        assert mock_camera.init.call_count == 1
        
        camera.shutdown()


def test_standby_mode_and_resume(mock_gphoto2):
    with patch('backend.camera_service.gp', mock_gphoto2):
        
        from backend.camera_service import CameraService
        camera = CameraService(MagicMock())
        
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


def test_enqueue_capture(mock_gphoto2):
    with patch('backend.camera_service.gp', mock_gphoto2):
        from backend.camera_service import CameraService
        camera = CameraService(MagicMock())
        
        # Test that enqueueing returns a job_id and puts it in the queue
        with patch.object(camera, 'standby') as mock_standby:
            with patch.object(camera, '_emit_job_state') as mock_emit:
                job_id = camera.enqueue_capture()
                
                assert job_id is not None
                assert len(job_id) == 8
                
                # Should have been placed in the queue
                assert not camera._cmd_queue.empty()
                cmd = camera._cmd_queue.get()
                assert cmd["type"] == "CAPTURE"
                assert cmd["job_id"] == job_id
                
                # Should have called standby and emitted pending
                mock_standby.assert_called_once()
                mock_emit.assert_called_once_with(job_id, "pending")

        camera.shutdown()

def test_execute_capture_job_success(mock_gphoto2):
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
        
        camera = CameraService(MagicMock())
        camera.init()
        
        job_id = "test_job"
        
        # Spy on event emitting
        with patch.object(camera, '_emit_job_state') as mock_emit:
            camera._execute_capture_job(job_id)
            
            # Verify the sequence of SSE events emitted
            assert mock_emit.call_count == 4
            mock_emit.assert_any_call(job_id, "started")
            mock_emit.assert_any_call(job_id, "fired")
            mock_emit.assert_any_call(job_id, "downloading")
            
            # The final completed event should have the filename
            final_call = mock_emit.call_args_list[-1]
            assert final_call[0][0] == job_id
            assert final_call[0][1] == "completed"
            assert final_call[1]["filename"] == f"capture_{job_id}.jpg"

        camera.shutdown()

def test_execute_capture_job_retries_once_then_succeeds(mock_gphoto2):
    """First trigger attempt fails; the backend retries once internally and
    the job still completes — the frontend must never see a 'failed' event
    or have to orchestrate the retry itself (Rule 14)."""
    with patch('backend.camera_service.gp', mock_gphoto2):
        from backend.camera_service import CameraService

        mock_camera = mock_gphoto2.Camera.return_value
        mock_camera.init.return_value = 0

        mock_camera_file = MagicMock()
        mock_camera_file.get_data_and_size.return_value = (b'fake_data', 9)
        mock_camera.file_get.return_value = mock_camera_file
        mock_camera.wait_for_event.return_value = (mock_gphoto2.GP_EVENT_TIMEOUT, None)

        # First capture() call fails, second succeeds
        mock_camera.capture.side_effect = [Exception("USB busy"), MagicMock()]

        camera = CameraService(MagicMock())
        # The background preview worker thread also calls time.sleep() (pacing,
        # reconnect backoff) and would otherwise race with the module-level
        # time.sleep patch below, polluting mock_sleep's call list.
        with patch.object(camera, '_start_worker'):
            camera.init()
        camera.connected = True  # capture() failure sets this False; force back for the retry

        job_id = "test_job_retry"

        with patch('backend.camera_service.time.sleep') as mock_sleep, \
             patch.object(camera, 'init') as mock_init, \
             patch.object(camera, '_emit_job_state') as mock_emit:
            mock_init.side_effect = lambda: setattr(camera, 'connected', True)

            camera._execute_capture_job(job_id)

            # One pre-capture preview-release settle + one retry delay.
            mock_sleep.assert_any_call(camera.PREVIEW_RELEASE_SETTLE_S)
            mock_sleep.assert_any_call(camera.CAPTURE_RETRY_DELAY_S)
            assert mock_camera.capture.call_count == 2

            statuses = [call.args[1] for call in mock_emit.call_args_list]
            assert statuses.count("fired") == 2
            assert "failed" not in statuses
            assert statuses[-1] == "completed"

        camera.shutdown()

def test_execute_capture_job_fails_after_retry(mock_gphoto2):
    """Both attempts fail — exactly one 'failed' event reaches the frontend,
    after the single retry delay."""
    with patch('backend.camera_service.gp', mock_gphoto2):
        from backend.camera_service import CameraService

        mock_camera = mock_gphoto2.Camera.return_value
        mock_camera.init.return_value = 0
        mock_camera.wait_for_event.return_value = (mock_gphoto2.GP_EVENT_TIMEOUT, None)
        mock_camera.capture.side_effect = Exception("USB busy")

        camera = CameraService(MagicMock())
        # The background preview worker thread also calls time.sleep() (pacing,
        # reconnect backoff) and would otherwise race with the module-level
        # time.sleep patch below, polluting mock_sleep's call list.
        with patch.object(camera, '_start_worker'):
            camera.init()

        job_id = "test_job_fail"

        with patch('backend.camera_service.time.sleep') as mock_sleep, \
             patch.object(camera, 'init') as mock_init, \
             patch.object(camera, '_emit_job_state') as mock_emit:
            mock_init.side_effect = lambda: setattr(camera, 'connected', True)

            camera._execute_capture_job(job_id)

            # One pre-capture preview-release settle + one retry delay.
            mock_sleep.assert_any_call(camera.PREVIEW_RELEASE_SETTLE_S)
            mock_sleep.assert_any_call(camera.CAPTURE_RETRY_DELAY_S)
            assert mock_camera.capture.call_count == 2

            statuses = [call.args[1] for call in mock_emit.call_args_list]
            assert statuses.count("failed") == 1
            assert statuses[-1] == "failed"

        camera.shutdown()

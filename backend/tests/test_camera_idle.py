import time
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def anyio_backend():
    # anyio's pytest plugin defaults to parametrizing over asyncio + trio;
    # trio isn't a project dependency, so pin to asyncio only.
    return "asyncio"


def test_watchdog_fires_despite_repeated_capture_errors(mock_gphoto2):
    """
    Regression test: the idle watchdog must run every worker-loop iteration,
    not just after a successful capture_preview(). Previously, a camera that
    errors on every attempt (but never 6 times in a row, so it never trips a
    full disconnect) could keep the worker alive indefinitely without ever
    reaching the watchdog check, so live view never rested even though no
    viewer had requested a preview in a long time.
    """
    with patch('backend.camera.device.gp', mock_gphoto2):
        mock_gphoto2.GP_EVENT_TIMEOUT = 2
        mock_camera = mock_gphoto2.Camera.return_value
        mock_camera.wait_for_event.return_value = (mock_gphoto2.GP_EVENT_TIMEOUT, None)
        # Every preview attempt fails, simulating the intermittent
        # "[-1] Unspecified error" bursts seen on real hardware.
        mock_camera.capture_preview.side_effect = Exception("[-1] Unspecified error")

        from backend.camera.service import CameraService
        camera = CameraService(MagicMock())
        camera.connected = True
        camera.camera = mock_camera

        # Simulate "nobody has requested a preview in a while".
        camera._preview._last_preview_request = \
            time.monotonic() - (camera._preview._preview_idle_timeout + 1)

        # Run exactly one iteration of the worker loop.
        camera._preview._shutdown.is_set = MagicMock(side_effect=[False, False, True])

        with patch('time.sleep'):
            camera._preview._worker_loop()

        # The watchdog must have paused the worker on this very first
        # iteration, before any capture was even attempted.
        assert camera._gate.preview_armed() is False
        mock_camera.capture_preview.assert_not_called()


@pytest.mark.anyio
async def test_preview_generator_refreshes_watchdog_while_frameless(mock_gphoto2):
    """
    Regression test: an attached viewer must count as a preview request even
    while no frames are arriving (camera erroring or mid-re-init). Previously
    _last_preview_request was only refreshed when a frame was actually
    yielded, so 10s into an outage the watchdog judged the viewer absent and
    paused the worker — blocking the auto-heal while a guest stared at a
    black stream.
    """
    with patch('backend.camera.device.gp', mock_gphoto2):
        from backend.camera.service import CameraService
        camera = CameraService(MagicMock())
        camera.connected = True
        preview = camera._preview

        stale = time.monotonic() - 100
        refreshes = 0

        def fake_wait_for_frame(timeout):
            nonlocal refreshes
            if preview._last_preview_request != stale:
                refreshes += 1
            # Re-stale it: the generator must refresh again before the next
            # poll, not just once on entry.
            preview._last_preview_request = stale
            return False  # never a frame

        preview._wait_for_frame = fake_wait_for_frame

        with patch.object(preview, 'start_worker'), patch.object(preview, 'resume'):
            gen = camera.preview_generator()
            with pytest.raises(StopAsyncIteration):
                await gen.__anext__()

        # One refresh per poll slice until the MAX_IDLE_S self-close.
        assert refreshes == 30.0 / 0.5


@pytest.mark.anyio
async def test_preview_generator_self_closes_when_idle(mock_gphoto2):
    """
    Regression test: preview_generator() must not loop forever if nothing is
    producing frames. Some browsers never close the underlying connection for
    a multipart/x-mixed-replace stream when the <img> unmounts, so relying on
    client-disconnect detection alone left the generator (and its socket)
    orphaned indefinitely. It must self-close after MAX_IDLE_S of no frames.
    """
    with patch('backend.camera.device.gp', mock_gphoto2):
        from backend.camera.service import CameraService
        camera = CameraService(MagicMock())
        camera.connected = True
        preview = camera._preview

        call_count = 0

        def fake_wait_for_frame(timeout):
            nonlocal call_count
            call_count += 1
            return False  # never a frame

        preview._wait_for_frame = fake_wait_for_frame

        # Don't touch the (mocked) camera hardware or spin up a real thread.
        with patch.object(preview, 'start_worker'), patch.object(preview, 'resume'):
            gen = camera.preview_generator()
            with pytest.raises(StopAsyncIteration):
                await gen.__anext__()

        # POLL_SLICE=0.5s, MAX_IDLE_S=30.0s inside the generator.
        assert call_count == 30.0 / 0.5

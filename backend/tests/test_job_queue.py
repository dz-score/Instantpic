import pytest
import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from backend.settings import AppSettings
from backend.job_queue import JobQueue

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.fixture
def queue():
    """A JobQueue with its collaborators handed in, the way the lifespan builds it."""
    settings_svc = MagicMock()
    settings_svc.get.return_value = AppSettings()
    return JobQueue(print_svc=MagicMock(), settings=settings_svc)

@pytest.mark.anyio
async def test_job_queue_photo_processing(queue):
    with patch("backend.job_queue.process_photo_layout") as mock_process:
        with patch("backend.job_queue.enforce_circular_storage"):
            # Mock process_photo_layout
            mock_process.return_value = "final_photo.jpg"

            # The submitter supplies the completion callbacks (dependency-inverted;
            # the queue no longer reaches back into the state machine).
            on_success = AsyncMock()
            on_failure = AsyncMock()

            queue.start()

            # Enqueue a photo processing job
            await queue.enqueue({
                "type": "PROCESS_PHOTO",
                "images": ["raw1.jpg"],
                "layout": "single",
                "text": "Hello",
                "overlay_id": "none",
                "on_success": on_success,
                "on_failure": on_failure,
            })

            # Wait for the worker to process the job
            await queue.join()

            # Verify processing was called, including the overlay catalogue the queue
            # now reads off its injected settings rather than a module global.
            mock_process.assert_called_once_with(
                ["raw1.jpg"], "single", "Hello", "none", AppSettings().overlays
            )

            # Verify the success callback received the result; failure untouched.
            # No emit_previews on this job, so the preview list comes back empty
            # and the screens fall back to the raws.
            on_success.assert_awaited_once_with("final_photo.jpg", [])
            on_failure.assert_not_awaited()

            await queue.stop()


@pytest.mark.anyio
async def test_job_queue_emits_previews_when_asked(queue):
    """emit_previews routes the raws through the preview generator and hands the
    result to the submitter alongside the composite."""
    with patch("backend.job_queue.process_photo_layout") as mock_process:
        with patch("backend.job_queue.generate_previews") as mock_previews:
            with patch("backend.job_queue.enforce_circular_storage"):
                mock_process.return_value = "final_photo.jpg"
                mock_previews.return_value = ["preview_raw1.jpg"]

                on_success = AsyncMock()
                queue.start()

                await queue.enqueue({
                    "type": "PROCESS_PHOTO",
                    "images": ["raw1.jpg"],
                    "layout": "single",
                    "text": "Hello",
                    "overlay_id": "none",
                    "emit_previews": True,
                    "on_success": on_success,
                    "on_failure": AsyncMock(),
                })

                await queue.join()

                mock_previews.assert_called_once_with(["raw1.jpg"])
                on_success.assert_awaited_once_with("final_photo.jpg", ["preview_raw1.jpg"])

                await queue.stop()

@pytest.mark.anyio
async def test_job_queue_unknown_job(queue):
    with patch("backend.job_queue.log.warn") as mock_warn:
        queue.start()

        await queue.enqueue({
            "type": "UNKNOWN_GARBAGE"
        })

        await queue.join()

        mock_warn.assert_called_with("job_queue", "unknown_job", "Unknown job type: UNKNOWN_GARBAGE")

        await queue.stop()

@pytest.mark.anyio
async def test_job_queue_error_handling(queue):
    with patch("backend.job_queue.process_photo_layout") as mock_process:
        with patch("backend.job_queue.enforce_circular_storage"):
            # Simulate a crash during processing
            mock_process.side_effect = Exception("Out of memory!")

            on_success = AsyncMock()
            on_failure = AsyncMock()

            queue.start()

            await queue.enqueue({
                "type": "PROCESS_PHOTO",
                "images": ["raw1.jpg"],
                "layout": "single",
                "text": "Hello",
                "overlay_id": "none",
                "on_success": on_success,
                "on_failure": on_failure,
            })

            await queue.join()

            # Verify the failure callback caught the error; success untouched
            on_failure.assert_awaited_once_with("Out of memory!")
            on_success.assert_not_awaited()

            await queue.stop()


@pytest.mark.anyio
async def test_slow_print_does_not_block_the_processing_lane(queue):
    """The reason the lanes are split. A print can hold its worker for ~63s
    (CUPS timeout + retry). On a shared lane, the next guest's processing job
    queued behind it and their REVEAL spinner waited out someone else's paper
    jam. Processing must complete while the print is still stuck."""
    print_running = threading.Event()
    release_print = threading.Event()

    def blocking_print(filepath):
        print_running.set()
        release_print.wait(10)          # stands in for CUPS timing out
        return SimpleNamespace(success=True, error=None)

    queue._print_svc.print = blocking_print

    with patch("backend.job_queue.process_photo_layout") as mock_process:
        with patch("backend.job_queue.enforce_circular_storage"):
            mock_process.return_value = "final_photo.jpg"
            on_print = AsyncMock()
            on_process = AsyncMock()

            queue.start()

            await queue.enqueue({
                "type": "PRINT_PHOTO",
                "filename": "stuck.jpg",
                "on_success": on_print,
                "on_failure": AsyncMock(),
            })
            # Don't race the worker: wait until the print genuinely owns its lane.
            await asyncio.to_thread(print_running.wait, 10)

            await queue.enqueue({
                "type": "PROCESS_PHOTO",
                "images": ["raw1.jpg"],
                "layout": "single",
                "text": "",
                "overlay_id": "none",
                "on_success": on_process,
                "on_failure": AsyncMock(),
            })

            # The whole point: this drains while the printer is still hanging.
            await asyncio.wait_for(
                queue._get_queue(queue.PROCESS_LANE).join(), timeout=10
            )
            on_process.assert_awaited_once_with("final_photo.jpg", [])
            on_print.assert_not_awaited()       # print really is still stuck

            release_print.set()
            await asyncio.wait_for(queue.join(), timeout=10)
            on_print.assert_awaited_once_with("stuck.jpg")

            await queue.stop()


@pytest.mark.anyio
async def test_jobs_are_routed_to_their_lane(queue):
    """Printing is the only slow-external job; everything else, including an
    unrecognized type, belongs on the guest-facing lane."""
    assert queue._lane_for("PRINT_PHOTO") == queue.PRINT_LANE
    assert queue._lane_for("PROCESS_PHOTO") == queue.PROCESS_LANE
    assert queue._lane_for("PROCESS_FRAME") == queue.PROCESS_LANE
    assert queue._lane_for("UNKNOWN_GARBAGE") == queue.PROCESS_LANE

import pytest
import asyncio
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
            await queue._get_queue().join()

            # Verify processing was called, including the overlay catalogue the queue
            # now reads off its injected settings rather than a module global.
            mock_process.assert_called_once_with(
                ["raw1.jpg"], "single", "Hello", "none", AppSettings().overlays
            )

            # Verify the success callback received the result; failure untouched
            on_success.assert_awaited_once_with("final_photo.jpg")
            on_failure.assert_not_awaited()

            await queue.stop()

@pytest.mark.anyio
async def test_job_queue_unknown_job(queue):
    with patch("backend.job_queue.log.warn") as mock_warn:
        queue.start()

        await queue.enqueue({
            "type": "UNKNOWN_GARBAGE"
        })

        await queue._get_queue().join()

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

            await queue._get_queue().join()

            # Verify the failure callback caught the error; success untouched
            on_failure.assert_awaited_once_with("Out of memory!")
            on_success.assert_not_awaited()

            await queue.stop()

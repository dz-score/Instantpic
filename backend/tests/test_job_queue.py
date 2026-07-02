import pytest
import asyncio
from unittest.mock import AsyncMock, patch

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.mark.anyio
async def test_job_queue_photo_processing():
    with patch("backend.job_queue.process_photo_layout") as mock_process:
        with patch("backend.job_queue.enforce_circular_storage"):
            from backend.job_queue import job_queue

            # Mock process_photo_layout
            mock_process.return_value = "final_photo.jpg"

            # The submitter supplies the completion callbacks (dependency-inverted;
            # the queue no longer reaches back into the state machine).
            on_success = AsyncMock()
            on_failure = AsyncMock()

            job_queue.start()

            # Enqueue a photo processing job
            await job_queue.enqueue({
                "type": "PROCESS_PHOTO",
                "images": ["raw1.jpg"],
                "layout": "single",
                "text": "Hello",
                "overlay_id": "none",
                "on_success": on_success,
                "on_failure": on_failure,
            })

            # Wait for the worker to process the job
            await job_queue._get_queue().join()

            # Verify processing was called
            mock_process.assert_called_once_with(["raw1.jpg"], "single", "Hello", "none")

            # Verify the success callback received the result; failure untouched
            on_success.assert_awaited_once_with("final_photo.jpg")
            on_failure.assert_not_awaited()

            await job_queue.stop()

@pytest.mark.anyio
async def test_job_queue_unknown_job():
    with patch("backend.job_queue.log.warn") as mock_warn:
        from backend.job_queue import job_queue

        job_queue.start()

        await job_queue.enqueue({
            "type": "UNKNOWN_GARBAGE"
        })

        await job_queue._get_queue().join()

        mock_warn.assert_called_with("job_queue", "unknown_job", "Unknown job type: UNKNOWN_GARBAGE")

        await job_queue.stop()

@pytest.mark.anyio
async def test_job_queue_error_handling():
    with patch("backend.job_queue.process_photo_layout") as mock_process:
        with patch("backend.job_queue.enforce_circular_storage"):
            from backend.job_queue import job_queue

            # Simulate a crash during processing
            mock_process.side_effect = Exception("Out of memory!")

            on_success = AsyncMock()
            on_failure = AsyncMock()

            job_queue.start()

            await job_queue.enqueue({
                "type": "PROCESS_PHOTO",
                "images": ["raw1.jpg"],
                "layout": "single",
                "text": "Hello",
                "overlay_id": "none",
                "on_success": on_success,
                "on_failure": on_failure,
            })

            await job_queue._get_queue().join()

            # Verify the failure callback caught the error; success untouched
            on_failure.assert_awaited_once_with("Out of memory!")
            on_success.assert_not_awaited()

            await job_queue.stop()

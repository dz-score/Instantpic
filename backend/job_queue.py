import asyncio
import os
from backend.logger import log
from backend.photo_processor import process_photo_layout
from backend.print_service import print_svc
from backend import storage
from backend.storage import enforce_circular_storage

class JobQueue:
    """Async worker that runs photo-processing jobs off the event loop.

    The queue reports results through per-job ``on_success`` / ``on_failure``
    coroutines supplied by the submitter. It does not know or import whoever
    consumes those results, so there is no dependency back onto the state
    machine (the submitter owns that wiring).
    """

    def __init__(self):
        self._queue = None
        self._worker_task = None
        self._shutdown = False
        self._background_tasks = set()

    def _get_queue(self):
        if self._queue is None:
            self._queue = asyncio.Queue()
        return self._queue

    async def enqueue(self, job_data: dict):
        log.debug("job_queue", "enqueue_job", f"Enqueuing job type: {job_data.get('type')}")
        await self._get_queue().put(job_data)

    async def _worker(self):
        log.info("job_queue", "worker_start", "Job Queue worker started")
        while not self._shutdown:
            try:
                # Wait for a job
                job_data = await self._get_queue().get()
                if self._shutdown:
                    break
                    
                job_type = job_data.get("type")
                log.info("job_queue", "job_start", f"Processing job type: {job_type}")

                on_success = job_data.get("on_success")
                on_failure = job_data.get("on_failure")

                try:
                    if job_type == "PROCESS_PHOTO" or job_type == "PROCESS_FRAME":
                        images = job_data.get("images")
                        layout = job_data.get("layout")
                        text = job_data.get("text", "")
                        overlay_id = job_data.get("overlay_id", "none")

                        # Process photo synchronously in a thread pool to avoid blocking asyncio
                        loop = asyncio.get_running_loop()
                        filename = await loop.run_in_executor(
                            None,
                            process_photo_layout,
                            images, layout, text, overlay_id
                        )

                        # Cleanup storage in background
                        task = asyncio.create_task(self._run_cleanup())
                        self._background_tasks.add(task)
                        task.add_done_callback(self._background_tasks.discard)

                        # Hand the result back to whoever submitted the job.
                        if on_success:
                            await on_success(filename)

                    elif job_type == "PRINT_PHOTO":
                        # Printing blocks (mock sleeps; CUPS shells out for up to
                        # ~60s incl. retry), so run it in the thread pool and
                        # report the real success/failure back to the submitter.
                        filename = job_data.get("filename")
                        filepath = os.path.join(storage.PHOTOS_DIR, filename)

                        loop = asyncio.get_running_loop()
                        result = await loop.run_in_executor(None, print_svc.print, filepath)

                        if result.success:
                            if on_success:
                                await on_success(filename)
                        elif on_failure:
                            await on_failure(result.error or "Print failed")

                    else:
                        log.warn("job_queue", "unknown_job", f"Unknown job type: {job_type}")

                except Exception as e:
                    log.error("job_queue", "job_error", f"Error processing job {job_type}: {e}")
                    if on_failure:
                        await on_failure(str(e))
                finally:
                    self._get_queue().task_done()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("job_queue", "worker_error", f"Job Queue worker encountered an error: {e}")
                await asyncio.sleep(1)
                
        log.info("job_queue", "worker_stop", "Job Queue worker stopped")

    async def _run_cleanup(self):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, enforce_circular_storage)

    def start(self):
        self._shutdown = False
        self._queue = asyncio.Queue()
        self._worker_task = asyncio.create_task(self._worker())

    async def stop(self):
        self._shutdown = True
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
                
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)

# Global Singleton
job_queue = JobQueue()

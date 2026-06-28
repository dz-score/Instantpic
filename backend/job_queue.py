import asyncio
from backend.logger import log
from backend.photo_processor import process_photo_layout
from backend.storage import enforce_circular_storage
from backend.state_machine import state_machine

class JobQueue:
    def __init__(self):
        self._queue = None
        self._worker_task = None
        self._shutdown = False

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
                        asyncio.create_task(self._run_cleanup())
                        
                        # Update state machine
                        if job_type == "PROCESS_PHOTO":
                            await state_machine.job_photo_processed(filename, images)
                        else:
                            await state_machine.job_frame_processed(filename)
                            
                    else:
                        log.warn("job_queue", "unknown_job", f"Unknown job type: {job_type}")
                        
                except Exception as e:
                    log.error("job_queue", "job_error", f"Error processing job {job_type}: {e}")
                    await state_machine.job_failed(str(e))
                finally:
                    self._get_queue().task_done()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("job_queue", "worker_error", f"Job Queue worker encountered an error: {e}")
                
        log.info("job_queue", "worker_stop", "Job Queue worker stopped")

    async def _run_cleanup(self):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, enforce_circular_storage)

    def start(self):
        self._shutdown = False
        self._worker_task = asyncio.create_task(self._worker())

    async def stop(self):
        self._shutdown = True
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

# Global Singleton
job_queue = JobQueue()

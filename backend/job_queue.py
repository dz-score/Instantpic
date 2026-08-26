import asyncio
import os
from backend.settings import SettingsService
from backend.logger import log
from backend.photo_processor import generate_previews, process_photo_layout
from backend.print_service import PrintService
from backend import storage
from backend.storage import enforce_circular_storage

class JobQueue:
    """Async workers that run photo-processing and print jobs off the event loop.

    Two lanes, not one, because the two workloads have opposite profiles.
    Processing sits on the guest's critical path — the REVEAL spinner is
    waiting on it — and takes seconds. A print shells out to CUPS and blocks
    for up to ~63s in the worst case (30s timeout + RETRY_DELAY_S + a 30s
    retry). Sharing a single serial lane meant a guest who tapped "Another"
    could queue their processing job behind the *previous* guest's retrying
    print and sit watching the spinner for a minute over someone else's paper
    jam. Splitting them decouples the guest-visible path from the slow external
    device; each lane stays strictly serial in itself, so two prints still
    never overlap and neither do two processing jobs.

    The queue reports results through per-job ``on_success`` / ``on_failure``
    coroutines supplied by the submitter. It does not know or import whoever
    consumes those results, so there is no dependency back onto the state
    machine (the submitter owns that wiring).

    The printer and settings are handed in by the composition root rather than
    imported, so a test can drive the queue against doubles.
    """

    PROCESS_LANE = "process"
    PRINT_LANE = "print"
    LANES = (PROCESS_LANE, PRINT_LANE)

    def __init__(self, print_svc: PrintService, settings: SettingsService):
        self._print_svc = print_svc
        self._settings = settings
        self._queues = {lane: None for lane in self.LANES}
        self._worker_tasks = []
        self._shutdown = False
        self._background_tasks = set()

    @classmethod
    def _lane_for(cls, job_type: str) -> str:
        """Which lane a job type runs in. Printing is the slow external one;
        everything else is guest-facing processing."""
        return cls.PRINT_LANE if job_type == "PRINT_PHOTO" else cls.PROCESS_LANE

    def _get_queue(self, lane: str):
        if self._queues[lane] is None:
            self._queues[lane] = asyncio.Queue()
        return self._queues[lane]

    async def enqueue(self, job_data: dict):
        job_type = job_data.get("type")
        lane = self._lane_for(job_type)
        log.debug("job_queue", "enqueue_job", f"Enqueuing job type: {job_type} on {lane} lane")
        await self._get_queue(lane).put(job_data)

    async def join(self):
        """Block until every lane has drained. For shutdown and for tests —
        callers should not have to know how many lanes there are."""
        for lane in self.LANES:
            await self._get_queue(lane).join()

    async def _worker(self, lane: str):
        log.info("job_queue", "worker_start", f"Job Queue worker started ({lane} lane)")
        while not self._shutdown:
            try:
                # Wait for a job
                job_data = await self._get_queue(lane).get()
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
                            images, layout, text, overlay_id,
                            self._settings.get().overlays,
                        )

                        # Screen previews of the individual shots, for the jobs
                        # whose output the guest actually looks at. Same thread
                        # pool, still inside the wait the REVEAL spinner covers.
                        previews = []
                        if job_data.get("emit_previews"):
                            previews = await loop.run_in_executor(
                                None, generate_previews, images
                            )

                        # Cleanup storage in background
                        task = asyncio.create_task(self._run_cleanup())
                        self._background_tasks.add(task)
                        task.add_done_callback(self._background_tasks.discard)

                        # Hand the result back to whoever submitted the job.
                        if on_success:
                            await on_success(filename, previews)

                    elif job_type == "PRINT_PHOTO":
                        # Printing blocks (mock sleeps; CUPS shells out for up to
                        # ~60s incl. retry), so run it in the thread pool and
                        # report the real success/failure back to the submitter.
                        filename = job_data.get("filename")
                        filepath = os.path.join(storage.PHOTOS_DIR, filename)

                        loop = asyncio.get_running_loop()
                        result = await loop.run_in_executor(None, self._print_svc.print, filepath)

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
                    self._get_queue(lane).task_done()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("job_queue", "worker_error", f"Job Queue worker encountered an error: {e}")
                await asyncio.sleep(1)
                
        log.info("job_queue", "worker_stop", f"Job Queue worker stopped ({lane} lane)")

    async def _run_cleanup(self):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, enforce_circular_storage, self._settings.get())

    def start(self):
        self._shutdown = False
        self._worker_tasks = []
        for lane in self.LANES:
            self._queues[lane] = asyncio.Queue()
            self._worker_tasks.append(asyncio.create_task(self._worker(lane)))

    async def stop(self):
        self._shutdown = True
        # Cancel every lane first, then await them: cancelling one at a time
        # would let a lane keep running while an earlier one is still winding
        # down, which on shutdown is just a longer window for new work to land.
        for task in self._worker_tasks:
            task.cancel()
        for task in self._worker_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._worker_tasks = []

        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)

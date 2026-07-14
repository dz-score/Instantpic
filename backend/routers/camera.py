import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.deps import get_camera, require_camera

router = APIRouter(prefix="/api/camera", tags=["camera"])


class CameraSettingsRequest(BaseModel):
    settings: dict


@router.get("/preview")
async def camera_preview(camera=Depends(require_camera)):
    return StreamingResponse(
        camera.preview_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Age": "0",
            "Cache-Control": "no-cache, no-store, must-revalidate, private",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx proxy buffering
        }
    )


@router.post("/capture")
async def camera_capture(camera=Depends(require_camera)):
    try:
        job_id = camera.enqueue_capture()
        return {"status": "enqueued", "job_id": job_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/standby")
async def camera_standby(camera=Depends(require_camera)):
    try:
        camera.standby()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/resume")
async def camera_resume(camera=Depends(require_camera)):
    try:
        camera.resume_preview()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config")
async def camera_config_get(camera=Depends(require_camera)):
    # USB round-trips under the camera lock — if the worker is inside a ~3s
    # preview stall this blocks until it releases, so keep it off the event
    # loop (same for the setter below).
    return await anyio.to_thread.run_sync(camera.get_settings)


@router.post("/config")
async def camera_config_set(req: CameraSettingsRequest, camera=Depends(require_camera)):
    try:
        await anyio.to_thread.run_sync(camera.set_settings, req.settings)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def camera_status(camera=Depends(get_camera)):
    # Unlike the routes above, an absent camera is a reportable status here,
    # not a 501 — the frontend polls this to render the disconnected state.
    if camera is None:
        return {"connected": False, "is_capturing": False, "error": "gphoto2 not installed"}
    return camera.get_status()

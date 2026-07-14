import socket

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.settings import AppSettings
from backend.deps import get_print_service, get_settings
from backend.logger import log
from backend.print_service import PrintService

router = APIRouter(tags=["system"])


class EmergencyRequest(BaseModel):
    action: str  # 'restart_booth', 'restart_camera', 'restart_printer', 'clear_queue'


@router.get("/api/health")
async def health_check():
    """Health check endpoint for connection watchdog."""
    return {"status": "ok"}


@router.get("/api/printer/status")
async def printer_status(print_svc: PrintService = Depends(get_print_service)):
    """Get current printer status (connected, ready, errors)."""
    status = print_svc.get_status()
    return status.to_dict()


@router.get("/api/diagnostics")
async def get_diagnostics(
    settings: AppSettings = Depends(get_settings),
    print_svc: PrintService = Depends(get_print_service),
):
    from backend.diagnostics import get_diagnostics
    return get_diagnostics(settings, print_svc)


@router.post("/api/emergency")
async def emergency_action(req: EmergencyRequest):
    from backend.diagnostics import execute_emergency
    log.warn("system", "system_emergency", f"Emergency action triggered: {req.action}", data={"action": req.action})
    result = execute_emergency(req.action)
    return result


def _get_lan_ip():
    """Get the machine's LAN IP address."""
    try:
        # Connect to an external address to determine the outbound interface
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


@router.get("/api/network-info")
async def get_network_info(settings: AppSettings = Depends(get_settings)):
    """Return the booth's LAN IP and port for QR code URL generation."""
    ip = _get_lan_ip()
    port = getattr(settings, "port", 8000)
    return {
        "ip": ip,
        "port": port,
        "base_url": f"http://{ip}:{port}",
    }

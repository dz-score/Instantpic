import socket

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.settings import AppSettings
from backend.deps import get_led, get_print_service, get_settings, get_state_machine
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
    led=Depends(get_led),
):
    from backend.diagnostics import get_diagnostics
    return get_diagnostics(settings, print_svc, led)


@router.post("/api/led/test")
async def test_led(led=Depends(get_led), sm=Depends(get_state_machine)):
    """Send one PING and report the round trip.

    Refused outside ATTRACT: this injects a command into the same single-owner
    queue the booth uses, and the protocol allows one command in flight
    (Docs/LED_PROTOCOL.md). During a capture sequence that queue is on the path
    between the countdown ending and the shutter, and nobody should be able to
    lengthen it from the admin panel by tapping a diagnostic button.
    """
    screen = (await sm.get_state()).screen
    if screen != "ATTRACT":
        raise HTTPException(
            status_code=409,
            detail=f"Booth is busy ({screen}) — test the ring from the idle screen",
        )
    result = await led.ping()
    log.info("system", "led_test", f"LED test: {result}", data=result)
    return result


# Channel names the admin panel uses, mapped to the firmware's TEST argument.
# Named here rather than in the UI so the two cannot drift, and so the numbers
# stay an implementation detail of the protocol (Docs/LED_PROTOCOL.md).
LED_TEST_CHANNELS = {"all": 0, "red": 1, "green": 2, "blue": 3, "white": 4}


class LedChannelRequest(BaseModel):
    channel: str


@router.post("/api/led/channel")
async def led_channel(
    req: LedChannelRequest,
    led=Depends(get_led),
    sm=Depends(get_state_machine),
):
    """Light one die across the whole ring, for checking the strip.

    `off` returns it to Idle. Refused outside ATTRACT for the same reason the
    ping is: it queues onto the single-owner queue the shutter waits on, and
    full white for two minutes is not something the admin panel should be able
    to start underneath a guest.
    """
    name = req.channel.strip().lower()
    if name != "off" and name not in LED_TEST_CHANNELS:
        raise HTTPException(status_code=400, detail=f"Unknown channel {req.channel!r}")

    screen = (await sm.get_state()).screen
    if screen != "ATTRACT":
        raise HTTPException(
            status_code=409,
            detail=f"Booth is busy ({screen}) — test the strip from the idle screen",
        )
    if not led.enabled:
        raise HTTPException(status_code=409, detail="LED ring is disabled")

    if name == "off":
        await led.idle()
        await led.drain()
        log.info("system", "led_channel", "LED test ended")
        return {"ok": True, "channel": "off", "reply": None}

    reply = await led.test_channel(LED_TEST_CHANNELS[name])
    log.info("system", "led_channel", f"LED test channel {name} -> {reply}")
    return {"ok": reply == "OK TEST", "channel": name, "reply": reply}


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

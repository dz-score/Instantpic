"""Application settings.

`AppSettings` is the schema. `SettingsService` owns the live instance for one
process and is constructed at the composition root (main.py's lifespan) — per
Rule 19, nothing below the entrypoint reaches for settings through a module
global; it receives a SettingsService or a plain AppSettings snapshot.

Memory is the source of truth. config.json is where that memory is persisted so it
survives a restart — it is an output, not an input, once the process is running. The
admin panel is the only writer; editing config.json by hand while the booth is running
will be silently overwritten by the next admin save, so hand-edits need a restart.
"""

import os
import json
import threading
import time
from pydantic import BaseModel, ValidationError
from typing import List, Dict, Literal, Optional

from backend.logger import log

# Re-exported as a module attribute so tests can monkeypatch it
# (conftest.isolate_config).
from backend.paths import CONFIG_PATH

class OverlayConfig(BaseModel):
    id: str
    name: str
    filename: str


class LedHttpConfig(BaseModel):
    # Host or IP only, no scheme or path. `idf.py monitor` prints it on
    # association: "wifi: connected — open http://192.168.x.x/".
    host: str = ""
    # Ordinary commands. Generous, because a retry storm on a congested 2.4 GHz
    # link is exactly the condition this has to survive without stalling the FSM.
    timeout_ms: int = 400
    # CAPTURE only. Deliberately tighter: this one sits between the guest's
    # countdown hitting zero and the shutter, so waiting longer for the ring
    # costs a visibly late photo. See Docs/LED_PROTOCOL.md on ERR TIMEOUT.
    capture_timeout_ms: int = 250


class LedConfig(BaseModel):
    """LED ring node. Off by default — the booth runs unchanged without one.

    Nested under a transport key so that adding a `uart:` block later is additive
    rather than a restructure (Docs/LED_UART_SWITCH.md).
    """
    enabled: bool = False
    transport: Literal["http"] = "http"
    # PING cadence. The node's watchdog trips at 10 s of silence
    # (MODE_LINK_TIMEOUT_MS), so this is a 5x margin. Only fires when the wire
    # has actually been idle — the watchdog counts any inbound line, so a busy
    # session needs no heartbeat at all.
    heartbeat_ms: int = 2000
    http: LedHttpConfig = LedHttpConfig()


class PrinterMockConfig(BaseModel):
    """Shapes MockPrinterDriver so a dev box can rehearse a real dye-sub.

    The defaults model a DNP DS-RX1HS on a 4x6 roll. They matter because the
    mock is not a stub here: on Windows it is the ONLY driver that ever runs
    (see PrintService._reload_driver), so every print path this project has —
    the printing animation's dwell, a failure reaching the guest, an operator
    watching media run down — is developed and tested against these numbers.
    Correct them from the real printer once it is on the bench.
    """
    # One 4x6 print, measured end to end. The guest watches the printing
    # animation for exactly this long, so a wrong value here makes the screen
    # look right in development and wrong at the event.
    job_duration_s: float = 13.0
    # Prints on a full roll. Changing this is "load a new roll" — the driver
    # reseeds its counter when the value moves.
    media_total: int = 700
    # A failure to rehearse. Everything except "none" is a fault this printer
    # actually has; set one, run a session, watch what the guest sees.
    #   submit_fails_once - lp rejects the first submission, accepts the retry
    #   offline           - nothing is accepted at all, status reports it
    #   out_of_media      - accepted, then the ribbon runs out mid-job
    #   abort_mid_job     - accepted, then a jam partway through
    fault: Literal[
        "none", "submit_fails_once", "offline", "out_of_media", "abort_mid_job"
    ] = "none"


class AppSettings(BaseModel):
    camera_backend: Literal["gphoto2", "mock"] = "gphoto2"
    admin_pin: str = "123456"
    welcome_message: str = "Create a Beautiful Memory"
    thank_you_message: str = "Thank you for celebrating with us!"
    # How many numbers the guest sees counted down (5 => "5,4,3,2,1").
    countdown_duration: int = 3
    # How fast those numbers tick, as a multiplier. The guest still sees all
    # countdown_duration numbers — they just run quicker, and the ring video's
    # playbackRate is scaled to match.
    #
    #     effective countdown (s) = countdown_duration / countdown_speed
    countdown_speed: float = 1.0
    # Pacing between shots in a multi-shot layout; owned by backend per Rule 14.
    # A "get ready" beat after the shutter so the guest can change pose before the
    # next count starts. Pure UX, and free — there is no shot-spacing constraint.
    shot_interval_ms: int = 500
    flash_enabled: bool = True
    max_photos_per_session: int = 3
    # Browser-side inactivity timeout. The frontend timer is the precise one —
    # it resets on touch, which the backend cannot see — but the FSM arms a
    # floor at session_timeout + SESSION_WATCHDOG_GRACE_S for the case that
    # timer never fires at all (dead kiosk tab). See _manage_watchdog.
    session_timeout: int = 120
    # Floor for a stalled capture sequence: if the browser or camera dies
    # mid-session, COUNTDOWN would strand forever. After this many seconds
    # with no shot progress the FSM resets to ATTRACT. Sized well above
    # countdown + capture + retry-once + shot interval; recovery is
    # backend-owned per Rule 14. (Ordinary completion never depends on the
    # browser: the camera reports straight to the FSM via callbacks.)
    capture_stall_timeout: float = 75.0
    show_names_on_photo: bool = True
    printer_name: str = "mock"
    printer_options: str = "fit-to-page media=4x6"
    # Only consulted when the mock driver is the one in use.
    printer_mock: PrinterMockConfig = PrinterMockConfig()
    # Cap on FILES in the photos dir, not on sessions or on keepsakes. One
    # 3-shot session that prints leaves 7: three raws (capture_*.jpg), three
    # screen previews (preview_capture_*.jpg) and one composite (photo_*.jpg).
    # So 1000 is roughly 140 sessions, not 1000 photos — size it from that,
    # or an SD card gets sized about 7x too small.
    max_photos: int = 1000
    disk_min_free_gb: float = 2.0
    # Circular storage will not delete a file younger than this. It runs after
    # every processing job and cannot ask the FSM what is on screen, so age is
    # its proxy for "still in use" — long enough to cover the session in front
    # of the guest plus a QR code they have not scanned yet. See
    # storage.enforce_circular_storage.
    storage_protect_recent_s: int = 1800
    couple_names: str = "Sarah & Michael"
    event_date: str = "June 14, 2026"
    default_text: str = "Sarah & Michael \u00b7 June 14, 2026"
    port: int = 8000
    selected_overlay: str = "none"
    wifi_network_name: str = "Our Wedding WiFi"
    led: LedConfig = LedConfig()
    # filename must match a real file in backend/overlays/. These defaults named
    # blush_floral.png / gold_glitter.png, neither of which was ever committed —
    # the shipped artwork is frame_floral.png / frame_gold_elegant.png. Nothing
    # complained because photo_processor fabricated a stand-in for any missing
    # overlay, so a booth on defaults printed drawn placeholders over the real
    # frames. That fabricator is gone, which makes a wrong filename here visible
    # (overlay_missing in the log) instead of silently substituted.
    overlays: List[OverlayConfig] = [
        OverlayConfig(id="none", name="No Frame", filename=""),
        OverlayConfig(id="blush_floral", name="Chic Blush Floral", filename="frame_floral.png"),
        OverlayConfig(id="gold_glitter", name="Elegant Gold Frame", filename="frame_gold_elegant.png")
    ]

def _quarantine_bad_config(path: str) -> str:
    """Move an unreadable config.json aside so the next boot starts clean.

    Returns the backup path, or "" if the file could not be moved.
    """
    backup = os.path.join(
        os.path.dirname(path), f"config.corrupt-{int(time.time())}.json"
    )
    try:
        os.replace(path, backup)
        return backup
    except OSError:
        return ""

def read_settings(path: str) -> AppSettings:
    """Read settings from disk, falling back to defaults if the file is unusable.

    MUST NOT raise. This runs during startup, so an exception here is not a bad
    config — it is a booth that will not boot. A file we cannot parse is quarantined
    and we carry on with defaults.
    """
    if not os.path.exists(path):
        return AppSettings()

    try:
        with open(path, "r") as f:
            return AppSettings(**json.load(f))
    except (json.JSONDecodeError, ValidationError, OSError, TypeError) as e:
        backup = _quarantine_bad_config(path)
        log.error(
            "config",
            "config_load_corrupt",
            f"config.json is unusable ({type(e).__name__}); starting from defaults",
            data={"error": str(e), "backup": backup or None},
        )
        return AppSettings()

def write_settings(path: str, settings: AppSettings):
    """Write settings to disk atomically.

    Write-in-place would truncate the file first, so a crash mid-write leaves a
    half-written config that read_settings then has to quarantine. Instead we write
    a temp file alongside it and os.replace() — atomic on both Windows and POSIX.
    The temp file must share a directory with the target: os.replace across volumes
    is not atomic.
    """
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(settings.model_dump(), f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        # Leave the existing config.json untouched, and don't litter a partial temp.
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


class SettingsService:
    """Owns the live settings for one process.

    Constructed and loaded at the composition root, then handed to whoever needs it.
    Hold the service (not the AppSettings it returns) when you need to see later
    edits — print_service reloads its driver per job so a printer swap takes effect.
    Hold the AppSettings snapshot when you need a consistent view for the duration of
    some work, which is what the FSM does across a capture sequence.
    """

    def __init__(self, path: Optional[str] = None):
        # No I/O here, per Rule 19 — call load(). A constructor that reads a file
        # can't be built in a test without that file existing.
        #
        # CONFIG_PATH is resolved here rather than as a default argument, because a
        # default binds once at import and would ignore a monkeypatched path.
        self._path = path if path is not None else CONFIG_PATH
        self._lock = threading.RLock()
        self._settings: Optional[AppSettings] = None

    def load(self) -> AppSettings:
        """Read the config file into memory. The composition root calls this once."""
        with self._lock:
            self._settings = read_settings(self._path)
            return self._settings

    def get(self) -> AppSettings:
        """The current settings. Memory only — never touches disk."""
        with self._lock:
            if self._settings is None:
                # Loudly, rather than lazily reading the file: a silent fallback here
                # is a second wiring mechanism, and it is what let tests quietly read
                # the developer's real config.json.
                raise RuntimeError(
                    "SettingsService.load() was never called. Settings are wired at "
                    "the composition root (main.py lifespan)."
                )
            return self._settings

    def update(self, updates: Dict) -> AppSettings:
        """Apply a dict of changes and persist them."""
        # The lock closes the read-modify-write race between two concurrent admin
        # POSTs, where the later write would otherwise clobber the earlier one.
        with self._lock:
            updated_data = self.get().model_dump()
            for key, value in updates.items():
                if key in updated_data:
                    updated_data[key] = value

            # REBIND, never mutate in place. The FSM takes an AppSettings snapshot and
            # holds it across a whole capture sequence (state_machine.py, the
            # shot_completed closure). Mutating the instance would reach into sequences
            # already in flight and change the pacing of shots mid-session; rebinding
            # leaves every existing holder on the snapshot it started with.
            self._settings = AppSettings(**updated_data)
            write_settings(self._path, self._settings)
            return self._settings

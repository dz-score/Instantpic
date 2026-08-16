from backend import jobs
from backend.settings import AppSettings


async def _noop(*_):
    pass


def test_process_job_keys_match_worker_contract():
    """The builder output is the FSM<->job_queue schema: exactly the keys
    job_queue._worker reads for PROCESS_PHOTO / PROCESS_FRAME."""
    expected = {"type", "images", "layout", "text", "overlay_id", "on_success", "on_failure"}

    photo = jobs.process_photo_job(["a.jpg"], "single", AppSettings(), _noop, _noop)
    frame = jobs.process_frame_job(["a.jpg"], "collage", "blush_floral", AppSettings(), _noop, _noop)

    # Only the capture pass asks for screen previews: its output is what REVEAL
    # and PICK_FAVORITE render. The frame pass feeds PRINTING, which shows no
    # individual shots, so it would be paying for previews nobody looks at.
    assert set(photo.keys()) == expected | {"emit_previews"}
    assert set(frame.keys()) == expected
    assert photo["emit_previews"] is True
    assert photo["type"] == "PROCESS_PHOTO"
    assert frame["type"] == "PROCESS_FRAME"
    assert frame["overlay_id"] == "blush_floral"


def test_print_job_keys_match_worker_contract():
    job = jobs.print_photo_job("photo_x.jpg", _noop, _noop)
    assert set(job.keys()) == {"type", "filename", "on_success", "on_failure"}
    assert job["type"] == "PRINT_PHOTO"
    assert job["filename"] == "photo_x.jpg"


def test_process_photo_overlay_defaults_to_configured_selection():
    settings = AppSettings(selected_overlay="gold_glitter")
    assert jobs.process_photo_job([], "single", settings, _noop, _noop)["overlay_id"] == "gold_glitter"
    settings = AppSettings(selected_overlay="")
    assert jobs.process_photo_job([], "single", settings, _noop, _noop)["overlay_id"] == "none"


def test_compose_banner_text_rules():
    # Names hidden -> empty, regardless of other fields.
    assert jobs.compose_banner_text(AppSettings(show_names_on_photo=False)) == ""
    # Names + date joined with the separator.
    s = AppSettings(couple_names="A & B", event_date="July 12, 2026")
    assert jobs.compose_banner_text(s) == "A & B · July 12, 2026"
    # Neither present -> fall back to default_text.
    s = AppSettings(couple_names="", event_date="", default_text="fallback")
    assert jobs.compose_banner_text(s) == "fallback"

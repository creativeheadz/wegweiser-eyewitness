"""Eyewitness Celery tasks: intake, capture reading, expiry, retention."""
from __future__ import annotations

import logging
import time

from app.extensions import celery

logger = logging.getLogger(__name__)


def _known_serials_for(cap) -> list:
    try:
        from app.models import db
        from app.eyewitness.intake import known_serials
        from app.eyewitness.models import EvidenceRequest
        from app.models.helpdesk import HelpdeskTicket
        req = db.session.get(EvidenceRequest, cap.request_id)
        if req is None:
            return []
        rec = db.session.get(HelpdeskTicket, req.helpdesk_ticket_id)
        return known_serials(rec) if rec is not None else []
    except Exception:
        return []


def process_capture(capture_id: int) -> dict:
    """Run the reader for one capture and store its verdict."""
    from app.models import db
    from app.eyewitness.models import Capture, EvidenceRequest
    cap = db.session.get(Capture, capture_id)
    if cap is None:
        return {"missing": True}
    cap.status = "processing"
    db.session.commit()
    try:
        with open(cap.file_path, "rb") as fh:
            data = fh.read()
        req = db.session.get(EvidenceRequest, cap.request_id)
        item = next((i for i in (req.items or [])
                     if i.get("id") == cap.item_id), None) if req else None
        template = (item or {}).get("template")
        if template == "sticker":
            from app.eyewitness.readers.sticker import read_sticker
            result = read_sticker(data,
                                  known_serials=_known_serials_for(cap),
                                  capture_id=cap.id,
                                  intake_run_id=(req.intake_run_id
                                                 if req else None))
        elif cap.kind == "photo":
            from app.eyewitness.readers.screen import read_screen
            result = read_screen(data, capture_id=cap.id,
                                 intake_run_id=(req.intake_run_id
                                                if req else None))
        elif cap.kind == "voice":
            from app.eyewitness.readers.voice import read_voice
            result = read_voice(data, cap.mime)
        else:  # video reading lands in Phase 3; hold the capture as read
            result = {"verdict": "ok", "retake": None,
                      "reading": {"stub": True,
                                  "summary": "video received"},
                      "injection_flag": False}
        cap.reading = result.get("reading")
        cap.retake = result.get("retake")
        cap.injection_flag = bool(result.get("injection_flag"))
        reading = result.get("reading") or {}
        cap.prompt_version = reading.get("prompt_version")
        cap.status = "ok" if result.get("verdict") == "ok" else "retake"
    except Exception as exc:  # one capture never kills the queue
        logger.exception("eyewitness read failed for %s", capture_id)
        cap.status = "failed"
        cap.error = str(exc)[:500]
    cap.processed_at = int(time.time())
    db.session.commit()
    # Release when this capture completes the request, and post a
    # follow-up note when it lands after release on a next-time item.
    try:
        from app.eyewitness import intake as ew_intake
        released = ew_intake.maybe_release_for_request(cap.request_id)
        if not released and cap.status == "ok":
            ew_intake.post_followup(cap.id)
    except Exception:
        logger.exception("eyewitness post-read release check failed")
    return {"capture_id": capture_id, "status": cap.status}


@celery.task(name="app.eyewitness.read_capture")
def read_capture(capture_id: int):
    return process_capture(capture_id)


@celery.task(name="app.eyewitness.run_intake")
def run_intake(intake_run_id: int):
    from app.eyewitness.intake import assess_run
    return assess_run(intake_run_id)


@celery.task(name="app.eyewitness.release_ticket")
def release_ticket(intake_run_id: int):
    from app.eyewitness.intake import release_run
    return {"released": release_run(intake_run_id)}


@celery.task(name="app.eyewitness.expire_requests")
def expire_requests():
    from app.eyewitness.intake import sweep_timeouts
    return sweep_timeouts()


@celery.task(name="app.eyewitness.retention_sweep")
def retention_sweep():
    # Phase 3: delete captures past EYEWITNESS_RETENTION_DAYS.
    return {"phase": 3}

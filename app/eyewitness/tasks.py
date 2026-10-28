"""Eyewitness Celery tasks. Phase 1: read_capture runs the reader;
run_intake/release/expire/retention are wired in Phases 2-3."""
from __future__ import annotations

import logging
import time

from app.extensions import celery

logger = logging.getLogger(__name__)


def process_capture(capture_id: int) -> dict:
    """Run the reader for one capture and store its verdict."""
    from app.models import db
    from app.eyewitness.models import Capture
    cap = db.session.get(Capture, capture_id)
    if cap is None:
        return {"missing": True}
    cap.status = "processing"
    db.session.commit()
    try:
        with open(cap.file_path, "rb") as fh:
            data = fh.read()
        if cap.kind == "photo":
            from app.eyewitness.readers.screen import read_screen
            result = read_screen(data)
        elif cap.kind == "voice":
            from app.eyewitness.readers.voice import read_voice
            result = read_voice(data, cap.mime)
        else:  # video lands in Phase 3; accept + stub until then
            result = {"verdict": "ok", "retake": None,
                      "reading": {"stub": True}, "injection_flag": False}
        cap.reading = result.get("reading")
        cap.retake = result.get("retake")
        cap.injection_flag = bool(result.get("injection_flag"))
        cap.status = "ok" if result.get("verdict") == "ok" else "retake"
    except Exception as exc:  # one capture never kills the queue
        logger.exception("eyewitness read failed for %s", capture_id)
        cap.status = "failed"
        cap.error = str(exc)[:500]
    cap.processed_at = int(time.time())
    db.session.commit()
    return {"capture_id": capture_id, "status": cap.status}


@celery.task(name="app.eyewitness.read_capture")
def read_capture(capture_id: int):
    return process_capture(capture_id)


@celery.task(name="app.eyewitness.run_intake")
def run_intake(intake_run_id: int):
    # Phase 2: Spark intake + request creation + hold/release.
    return {"phase": 2, "intake_run_id": intake_run_id}


@celery.task(name="app.eyewitness.release_ticket")
def release_ticket(intake_run_id: int):
    # Phase 2: summary note + queue the governor, exactly once.
    return {"phase": 2, "intake_run_id": intake_run_id}


@celery.task(name="app.eyewitness.expire_requests")
def expire_requests():
    from app.models import db
    from app.eyewitness.models import EvidenceRequest
    now = int(time.time())
    rows = (EvidenceRequest.query.filter_by(status="open")
            .filter(EvidenceRequest.expires_at < now).all())
    for row in rows:
        row.status = "expired"
    db.session.commit()
    return {"expired": len(rows)}


@celery.task(name="app.eyewitness.retention_sweep")
def retention_sweep():
    # Phase 3: delete captures past EYEWITNESS_RETENTION_DAYS.
    return {"phase": 3}

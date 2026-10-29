"""Eyewitness Celery tasks: intake, capture reading, expiry, retention."""
from __future__ import annotations

import logging
import time

from app.extensions import celery

logger = logging.getLogger(__name__)

# Photo templates that show a screen or a display, which Spark reads.
# Other photos (the whole device, the cables, custom items) get the
# quality gate only, so a clear photo of cables is never sent back for
# showing no screen.
SCREEN_TEMPLATES = frozenset({"screen_photo", "screen_photo_next_time",
                              "printer_panel"})

# Every item template routes to its reader by name. A template with
# no entry here falls back to its capture kind, and the test holds
# the table against the template catalogue so a new template cannot
# drift to the wrong reader.
TEMPLATE_READERS = {
    "screen_photo": "screen",
    "screen_photo_next_time": "screen",
    "printer_panel": "screen",
    "sticker": "sticker",
    "whole_device": "photo",
    "cables_back": "photo",
    "custom": "photo",
    "power_light_video": "video",
    "voice_note": "voice",
}


def reader_for(template: str | None, kind: str | None) -> str:
    """sticker, screen, photo, voice or video for one capture."""
    if template in TEMPLATE_READERS:
        return TEMPLATE_READERS[template]
    if kind == "photo":
        return "photo"
    if kind == "voice":
        return "voice"
    if kind == "video":
        return "video"
    return "video"


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
        reader = reader_for((item or {}).get("template"), cap.kind)
        # The request knows the tenant, so the reader client comes
        # from the tenant's backend choice. A tenant set to local
        # never reaches the Meta Model API from here.
        tenant_uuid = req.tenant_uuid if req else None
        client = None
        if tenant_uuid is not None:
            try:
                from app.eyewitness.meta.client import reader_client
                client = reader_client(tenant_uuid)
            except Exception:
                client = None
        if reader == "sticker":
            from app.eyewitness.readers.sticker import read_sticker
            result = read_sticker(data, client=client,
                                  known_serials=_known_serials_for(cap),
                                  capture_id=cap.id,
                                  intake_run_id=(req.intake_run_id
                                                 if req else None),
                                  tenant_uuid=tenant_uuid)
        elif reader == "screen":
            from app.eyewitness.readers.screen import read_screen
            result = read_screen(data, client=client, capture_id=cap.id,
                                 intake_run_id=(req.intake_run_id
                                                if req else None),
                                 tenant_uuid=tenant_uuid)
        elif reader == "photo":
            from app.eyewitness.readers.quality import read_photo
            result = read_photo(data)
        elif reader == "voice":
            from app.eyewitness.readers.voice import read_voice
            result = read_voice(data, cap.mime, client=client,
                                capture_id=cap.id,
                                intake_run_id=(req.intake_run_id
                                               if req else None),
                                tenant_uuid=tenant_uuid)
        else:
            from app.eyewitness.readers.video_lights import read_video
            result = read_video(data, cap.mime, client=client,
                                capture_id=cap.id,
                                intake_run_id=(req.intake_run_id
                                               if req else None),
                                tenant_uuid=tenant_uuid)
        cap.reading = result.get("reading")
        cap.retake = result.get("retake")
        cap.injection_flag = bool(result.get("injection_flag"))
        reading = result.get("reading") or {}
        cap.quality = reading.get("quality") or reading.get("stats")
        cap.prompt_version = reading.get("prompt_version")
        cap.status = "ok" if result.get("verdict") == "ok" else "retake"
    except Exception as exc:  # one capture never kills the queue
        logger.exception("eyewitness read failed for %s", capture_id)
        cap.status = "failed"
        cap.error = str(exc)[:500]
    cap.processed_at = int(time.time())
    db.session.commit()
    if cap.status == "ok":
        try:
            from app.eyewitness import callbacks as ew_callbacks
            ew_callbacks.fire(cap.request_id, "evidence.read",
                              capture_id=cap.id)
        except Exception:
            logger.exception("eyewitness evidence.read callback failed")
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
    from app.eyewitness.retention import sweep_purge
    return sweep_purge()


@celery.task(name="app.eyewitness.deliver_callback", bind=True,
             max_retries=4, default_retry_delay=60)
def deliver_callback(self, delivery_id: int):
    """One signed callback, retried with growing waits (Phase 5)."""
    from app.eyewitness.callbacks import deliver
    try:
        return deliver(delivery_id)
    except Exception as exc:
        countdown = 60 * (2 ** min(self.request.retries, 4))
        raise self.retry(exc=exc, countdown=countdown)

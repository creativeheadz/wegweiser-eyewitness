"""The intake agent and hold/release logic (brief 7.4, 7.6).

`on_new_ticket` is called from the inbound poll and from the help-link
submit path. It returns True when Eyewitness takes the ticket, in which
case the caller does not queue the governor. `assess_run` makes the one
Spark call, creates and delivers the evidence request (or holds it for
approval), and releases at once when no evidence is needed.
`release_run` posts the internal summary and queues the governor,
exactly once. Triggers: Done, every item resolved with the voice offer
settled, expiry, a technician pressing Release now, and the safety
timeout (EYEWITNESS_MAX_HOLD_HOURS, default 24).

Eyewitness uses its own Meta key through meta/client.py. With no key
set the paid step is skipped and the ticket goes to the governor
unchanged (brief section 9).
"""
from __future__ import annotations

import logging
import os
import time

from .requests import QUESTIONS, TEMPLATES

logger = logging.getLogger(__name__)

MISSING_KEYS = ("affected_device", "since_when", "how_often",
                "what_changed", "impact", "error_text")
PHYSICAL = ("bsod_or_boot_failure", "blinking_lights", "no_boot",
            "display_error", "printer_panel", "beeps", "other_physical",
            "none")

INTAKE_SCHEMA = {
    "type": "object",
    "properties": {
        "needs_evidence": {"type": "boolean"},
        "missing": {"type": "array",
                    "items": {"type": "string", "enum": list(MISSING_KEYS)}},
        "physical_symptom": {"type": "string", "enum": list(PHYSICAL)},
        "items": {"type": "array", "items": {
            "type": "object",
            "properties": {"template": {"type": "string",
                                        "enum": list(TEMPLATES)}},
            "required": ["template"], "additionalProperties": False}},
        "questions": {"type": "array", "items": {
            "type": "object",
            "properties": {"template": {"type": "string",
                                        "enum": list(QUESTIONS)}},
            "required": ["template"], "additionalProperties": False}},
        "reason_for_technician": {"type": "string"},
    },
    "required": ["needs_evidence", "missing", "physical_symptom",
                 "items", "questions", "reason_for_technician"],
    "additionalProperties": False,
}


def validate_intake_output(data) -> dict | None:
    """Validate Spark structured output. Invalid means no request."""
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("needs_evidence"), bool):
        return None
    missing = data.get("missing") or []
    if not isinstance(missing, list) or any(m not in MISSING_KEYS for m in missing):
        return None
    if data.get("physical_symptom", "none") not in PHYSICAL:
        return None
    items = data.get("items") or []
    if not isinstance(items, list) or len(items) > 4:
        return None
    for item in items:
        if not isinstance(item, dict) or item.get("template") not in TEMPLATES:
            return None
    questions = data.get("questions") or []
    if not isinstance(questions, list) or len(questions) > 2:
        return None
    for q in questions:
        if not isinstance(q, dict) or q.get("template") not in QUESTIONS:
            return None
    reason = data.get("reason_for_technician")
    if reason is not None and not isinstance(reason, str):
        return None
    return {"needs_evidence": data["needs_evidence"],
            "missing": list(missing),
            "physical_symptom": data.get("physical_symptom", "none"),
            "items": [{"template": i["template"]} for i in items],
            "questions": [{"template": q["template"]} for q in questions],
            "reason_for_technician": (reason or "")[:1000]}


def should_hold(origin: str, *, tenant_enabled: bool,
                connector_supported: bool = True) -> bool:
    """ORIGIN_WEGWEISER is never Eyewitness to judge (M1)."""
    if origin == "wegweiser":
        return False
    if origin not in ("desk", "help"):
        return False
    return bool(tenant_enabled and connector_supported)


def release_due(*, done: bool, all_resolved: bool, expired: bool,
                manual: bool, held_seconds: int,
                max_hold_seconds: int) -> bool:
    """Every release trigger from 7.6 in one place."""
    return bool(done or all_resolved or expired or manual
                or held_seconds >= max_hold_seconds)


def is_complete(items, latest: dict, voice_offer: str) -> bool:
    """True when every item has an ok or skipped capture and, where the
    customer skipped everything, the voice offer has been settled.

    `latest` maps item_id to the newest capture status for that item.
    A voice offer the customer accepted is not settled until a voice
    capture or Done arrives, so it returns False here.
    """
    if not items:
        return False
    states = []
    for item in items:
        status = latest.get(item.get("id"))
        if status not in ("ok", "skipped"):
            return False
        states.append(status)
    if states and all(s == "skipped" for s in states):
        return voice_offer == "declined"
    return True


def max_hold_seconds() -> int:
    try:
        hours = float(os.environ.get("EYEWITNESS_MAX_HOLD_HOURS", "24"))
    except ValueError:
        hours = 24.0
    return int(hours * 3600)


# -- context gathering ---------------------------------------------------

def _load_intake_prompt() -> str:
    path = os.path.join(os.path.dirname(__file__), "prompts",
                        "intake_v1.md")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _base_url() -> str:
    try:
        from flask import current_app, has_app_context
        if has_app_context():
            url = (current_app.config.get("APP_BASE_URL") or "").strip()
            if url:
                return url.rstrip("/")
    except Exception:
        pass
    return (os.environ.get("APP_BASE_URL")
            or "https://app.wegweiser.tech").rstrip("/")


def _device_status(deviceuuid):
    if not deviceuuid:
        return None
    from app.models.devices import DeviceStatus
    from app.models import db
    return db.session.get(DeviceStatus, deviceuuid)


def device_filter_facts(rec) -> dict:
    """Values the outgoing filter must keep off a customer page: the
    ticket device hostname, serials and logged-on user (7.8)."""
    facts: dict = {}
    if rec is None or not getattr(rec, "deviceuuid", None):
        return facts
    from app.models import Devices, db
    device = db.session.get(Devices, rec.deviceuuid)
    if device is not None:
        facts["hostname"] = getattr(device, "devicename", "") or ""
    status = _device_status(rec.deviceuuid)
    if status is not None:
        facts["serial"] = getattr(status, "system_serial", "") or ""
        facts["hardware_serial"] = facts["serial"]
        facts["username"] = getattr(status, "logged_on_user", "") or ""
        facts["logged_on_user"] = facts["username"]
    return facts


def known_serials(rec) -> list:
    status = _device_status(getattr(rec, "deviceuuid", None)) if rec else None
    if status is None:
        return []
    return [getattr(status, "system_serial", None),
            getattr(status, "baseboard_serial", None)]


def _ticket_words(rec) -> str:
    """The ticket opening words, the same source the governor reads."""
    words = ""
    if getattr(rec, "help_request_uuid", None):
        from app.models.helpdesk import HelpRequest
        from app.models import db
        req = db.session.get(HelpRequest, rec.help_request_uuid)
        if req is not None:
            words = f"{req.subject or ''}\n\n{req.description or ''}".strip()
    if not words and rec.provider == "zammad":
        from app.utilities.helpdesk_inbound import opening_text
        from app.utilities.zammad_sync import connected_client
        client, _ctx = connected_client(rec.tenant_uuid)
        if client is not None:
            words = opening_text(client, rec)
    title = rec.title or ""
    if title and not words.startswith(title):
        words = f"{title}\n\n{words}".strip()
    return words[:4000]


# -- the hook --------------------------------------------------------------

def on_new_ticket(rec) -> bool:
    """Take a new ticket for assessment, or leave it to the governor.

    Returns True when Eyewitness holds the ticket. Any failure here
    returns False so the governor runs as it does today.
    """
    try:
        from app.models import db
        from app.eyewitness.models import IntakeRun, TenantSettings
        settings = db.session.get(TenantSettings, rec.tenant_uuid)
        enabled = bool(settings and settings.enabled)
        supported = rec.provider == "zammad"
        if not should_hold(rec.origin, tenant_enabled=enabled,
                           connector_supported=supported):
            return False
        run = IntakeRun(tenant_uuid=rec.tenant_uuid,
                        helpdesk_ticket_id=rec.id,
                        deviceuuid=rec.deviceuuid,
                        origin=rec.origin, status="assessing")
        db.session.add(run)
        db.session.commit()
        try:
            from app.eyewitness.tasks import run_intake
            run_intake.delay(run.id)
        except Exception:
            logger.exception("eyewitness: queue failed, assessing inline")
            assess_run(run.id)
        return True
    except Exception:
        logger.exception("eyewitness: on_new_ticket failed, governor runs")
        try:
            from app.models import db
            db.session.rollback()
        except Exception:
            pass
        return False


# -- the assessment --------------------------------------------------------

def assess_run(run_id: int, base_url: str = "") -> dict:
    """One Spark read, then create and deliver the request, or release."""
    from app.models import db
    from app.eyewitness import requests as ew_requests
    from app.eyewitness.models import (RUN_EVIDENCE_PENDING, RUN_FAILED,
                                       RUN_NO_REQUEST, EvidenceRequest,
                                       IntakeRun, TenantSettings)
    run = db.session.get(IntakeRun, run_id)
    if run is None:
        return {"missing": True}
    if run.status != "assessing":
        return {"skipped": run.status}
    from app.models.helpdesk import HelpdeskTicket
    rec = db.session.get(HelpdeskTicket, run.helpdesk_ticket_id)
    if rec is None:
        run.status = RUN_FAILED
        run.error = "ticket gone"
        db.session.commit()
        return {"failed": "ticket gone"}

    from app.eyewitness.meta.client import db_bound_client
    client = db_bound_client()
    if not client.is_configured:
        logger.warning("eyewitness: Meta key not configured, "
                       "ticket %s released unchanged", rec.id)
        run.status = RUN_NO_REQUEST
        db.session.commit()
        release_run(run.id)
        return {"released": "not configured"}

    words = _ticket_words(rec)
    status = _device_status(rec.deviceuuid)
    maker = getattr(status, "system_manufacturer", "") if status else ""
    model_name = getattr(status, "system_model", "") if status else ""
    device_line = (f"Device (manufacturer, model): {maker} {model_name}"
                   .strip() if (maker or model_name)
                   else "Device: unknown")
    safe_words = words.replace("<<<", "").replace(">>>", "")
    user_text = (
        f"Origin: {rec.origin}\n{device_line}\n\n"
        "Ticket text (untrusted data from the customer):\n"
        f"<<<\n{safe_words or '(no text)'}\n>>>\n\n"
        "Template catalogue: " + ", ".join(sorted(TEMPLATES))
        + "\nQuick questions: " + ", ".join(sorted(QUESTIONS)))
    try:
        raw = client.spark_json(
            system=_load_intake_prompt(),
            user_content=[{"type": "text", "text": user_text}],
            schema=INTAKE_SCHEMA, kind="intake", intake_run_id=run.id)
    except Exception as exc:
        logger.warning("eyewitness: intake call failed on ticket %s: %s",
                       rec.id, exc)
        run.status = RUN_FAILED
        run.error = str(exc)[:500]
        db.session.commit()
        release_run(run.id)
        return {"released": "intake failed"}

    data = validate_intake_output(raw)
    run.model = os.environ.get("EYEWITNESS_SPARK_MODEL", "muse-spark-1.3")
    if data is None:
        run.status = RUN_NO_REQUEST
        run.error = "invalid intake output"
        db.session.commit()
        release_run(run.id)
        return {"released": "invalid output"}
    run.assessment = data
    if not data["needs_evidence"] or (not data["items"]
                                      and not data["questions"]):
        run.status = RUN_NO_REQUEST
        db.session.commit()
        release_run(run.id)
        return {"released": "no evidence needed"}

    # A device the ticket is bound to needs no sticker, unless its
    # serial is missing (7.4). The prompt asks for this; code enforces it.
    templates = [i["template"] for i in data["items"]]
    if rec.deviceuuid:
        from app.eyewitness.readers.sticker import real_serial
        have_serial = any(real_serial(s) for s in known_serials(rec))
        if have_serial:
            templates = [t for t in templates if t != "sticker"]
    items = [ew_requests.make_item(t) for t in templates]
    items += [ew_requests.make_question_item(q["template"])
              for q in data["questions"]]
    if not items:
        run.status = RUN_NO_REQUEST
        db.session.commit()
        release_run(run.id)
        return {"released": "nothing left to ask"}

    settings = db.session.get(TenantSettings, rec.tenant_uuid)
    auto_send = bool(settings.auto_send_templates) if settings else True
    existing = EvidenceRequest.query.filter_by(
        helpdesk_ticket_id=rec.id).count()
    approval = ew_requests.needs_approval(
        items, existing_requests=existing, auto_send_templates=auto_send)

    token = ew_requests.new_token()
    link = f"{(base_url or _base_url())}/eyewitness/{token}"
    expires = int(time.time()) + ew_requests.link_ttl_seconds(
        next_time=any(i.get("open_until") for i in items))
    from app.models import Tenants
    tenant = db.session.get(Tenants, rec.tenant_uuid)
    msp_name = getattr(tenant, "tenantname", None) or "Your IT support"
    text = ew_requests.render_customer_text(items, link, expires,
                                            msp_name=msp_name)
    from app.eyewitness.security import outgoing_filter
    hits = outgoing_filter(text, device_facts=device_filter_facts(rec),
                           allowed_url=link)
    status_value = "needs_approval" if (approval or hits) else "open"
    row = EvidenceRequest(
        tenant_uuid=rec.tenant_uuid, intake_run_id=run.id,
        helpdesk_ticket_id=rec.id, token=token, items=items,
        customer_text=text,
        reason_for_technician=data["reason_for_technician"],
        language="en", status=status_value, filter_hits=hits or None,
        expires_at=expires)
    db.session.add(row)
    run.status = RUN_EVIDENCE_PENDING
    db.session.commit()
    if row.status == "open":
        _deliver(rec, row)
    return {"request_id": row.id, "status": row.status,
            "filter_hits": hits}


def _deliver(rec, row) -> None:
    """Post the customer-visible note through the tenant connector."""
    try:
        from app.eyewitness.connectors.zammad import ZammadConnector
        connector, _reason = ZammadConnector.for_tenant(rec.tenant_uuid)
        if connector is None:
            row.delivered_via = {"connector": "zammad",
                                 "error": "not connected"}
        else:
            out = connector.post_public_note(rec.external_id,
                                             row.customer_text)
            row.delivered_via = {"connector": "zammad", **out}
        from app.models import db
        db.session.commit()
    except Exception as exc:
        logger.exception("eyewitness: delivery failed on ticket %s", rec.id)
        try:
            from app.models import db
            db.session.rollback()
            row.delivered_via = {"connector": "zammad",
                                 "error": str(exc)[:200]}
            db.session.commit()
        except Exception:
            pass


# -- release -----------------------------------------------------------------

def _captures_as_dicts(req_row) -> list[dict]:
    from app.eyewitness.models import Capture
    titles = {i.get("id"): i.get("title") for i in (req_row.items or [])}
    out = []
    caps = (Capture.query.filter_by(request_id=req_row.id)
            .order_by(Capture.created_at.asc(), Capture.id.asc()).all())
    for cap in caps:
        out.append({"id": cap.id, "item_id": cap.item_id,
                    "item_title": titles.get(cap.item_id) or cap.item_id,
                    "kind": cap.kind, "status": cap.status,
                    "reading": cap.reading, "retake": cap.retake,
                    "skip_reason": cap.skip_reason,
                    "injection_flag": bool(cap.injection_flag),
                    "file_path": cap.file_path, "mime": cap.mime})
    return out


def _ticket_meta_usd(run, req_row) -> float:
    from app.models import db
    from app.eyewitness.models import Capture, MetaCall
    total = (db.session.query(
        db.func.coalesce(db.func.sum(MetaCall.usd), 0))
        .filter(MetaCall.intake_run_id == run.id).scalar() or 0)
    if req_row is not None:
        cap_ids = [c.id for c in Capture.query.filter_by(
            request_id=req_row.id).all()]
        if cap_ids:
            total += (db.session.query(
                db.func.coalesce(db.func.sum(MetaCall.usd), 0))
                .filter(MetaCall.capture_id.in_(cap_ids)).scalar() or 0)
    return float(total or 0)


def release_run(run_id) -> bool:
    """Post the summary (when a request exists) and queue the governor.

    Exactly once: an atomic claim on the run row. A second call, from
    any trigger, returns False and does nothing.
    """
    from app.models import db
    from app.eyewitness.models import (RUN_RELEASED, EvidenceRequest,
                                       IntakeRun)
    run = db.session.get(IntakeRun, int(run_id))
    if run is None or run.status == RUN_RELEASED or run.released_at:
        return False
    now = int(time.time())
    claimed = (IntakeRun.query.filter_by(id=run.id)
               .filter(IntakeRun.status != RUN_RELEASED,
                       IntakeRun.released_at.is_(None))
               .update({"status": RUN_RELEASED, "released_at": now},
                       synchronize_session=False))
    db.session.commit()
    if not claimed:
        return False
    run = db.session.get(IntakeRun, int(run_id))
    from app.models.helpdesk import HelpdeskTicket
    rec = db.session.get(HelpdeskTicket, run.helpdesk_ticket_id)

    req_row = (EvidenceRequest.query.filter_by(intake_run_id=run.id)
               .order_by(EvidenceRequest.created_at.desc()).first())
    if req_row is not None and rec is not None and rec.provider == "zammad":
        try:
            from app.eyewitness.summary import build_summary
            from app.models import Devices
            device = (db.session.get(Devices, rec.deviceuuid)
                      if rec.deviceuuid else None)
            label = (getattr(device, "devicename", "") or "unknown")
            if rec.deviceuuid and label != "unknown":
                label += " (known from the ticket binding)"
            captures = _captures_as_dicts(req_row)
            missing = ((run.assessment or {}).get("missing") or [])
            body = build_summary(
                {"items": req_row.items or []}, captures,
                device_label=label, meta_usd=_ticket_meta_usd(run, req_row),
                missing=missing)
            from app.eyewitness.connectors.zammad import ZammadConnector
            connector, _reason = ZammadConnector.for_tenant(
                rec.tenant_uuid)
            if connector is not None:
                connector.post_internal_note(rec.external_id, body)
        except Exception:
            logger.exception("eyewitness: summary post failed on run %s",
                             run.id)
    try:
        from app.tasks.helpdesk import triage_helpdesk_ticket
        triage_helpdesk_ticket.delay(run.helpdesk_ticket_id)
    except Exception:
        logger.exception("eyewitness: governor queue failed on run %s",
                         run.id)
    return True


def _latest_statuses(req_row) -> dict:
    from app.eyewitness.models import Capture
    latest: dict = {}
    caps = (Capture.query.filter_by(request_id=req_row.id)
            .order_by(Capture.created_at.desc(), Capture.id.desc()).all())
    for cap in caps:
        latest.setdefault(cap.item_id, cap.status)
    return latest


def maybe_release_for_request(request_id, *, done: bool = False,
                              manual: bool = False) -> bool:
    """Release the run behind a request when a trigger fires (7.6)."""
    from app.models import db
    from app.eyewitness.models import EvidenceRequest, IntakeRun
    req_row = db.session.get(EvidenceRequest, int(request_id))
    if req_row is None or not req_row.intake_run_id:
        return False
    run = db.session.get(IntakeRun, req_row.intake_run_id)
    if run is None or run.status == "released":
        return False
    expired = req_row.status == "expired" or int(time.time()) >= int(
        req_row.expires_at)
    complete = is_complete(req_row.items or [],
                           _latest_statuses(req_row), req_row.voice_offer)
    held = int(time.time()) - int(run.created_at)
    if release_due(done=done, all_resolved=complete, expired=expired,
                   manual=manual, held_seconds=held,
                   max_hold_seconds=max_hold_seconds()):
        return release_run(run.id)
    return False


def post_followup(capture_id: int) -> bool:
    """A capture that arrives after release (a next-time item) posts a
    follow-up internal note. The governor does not run again (7.6)."""
    from app.models import db
    from app.eyewitness.models import Capture, EvidenceRequest, IntakeRun
    cap = db.session.get(Capture, int(capture_id))
    if cap is None or cap.status != "ok":
        return False
    req_row = db.session.get(EvidenceRequest, cap.request_id)
    if req_row is None or not req_row.intake_run_id:
        return False
    run = db.session.get(IntakeRun, req_row.intake_run_id)
    if run is None or run.status != "released":
        return False
    from app.models.helpdesk import HelpdeskTicket
    rec = db.session.get(HelpdeskTicket, req_row.helpdesk_ticket_id)
    if rec is None or rec.provider != "zammad":
        return False
    try:
        from app.eyewitness.summary import build_summary
        title = next((i.get("title") for i in (req_row.items or [])
                      if i.get("id") == cap.item_id), cap.item_id)
        body = build_summary(
            {"items": req_row.items or []},
            [{"id": cap.id, "item_id": cap.item_id, "item_title": title,
              "kind": cap.kind, "status": cap.status,
              "reading": cap.reading, "injection_flag": cap.injection_flag}],
            device_label="see ticket", meta_usd=0.0)
        body = "Follow-up capture after the ticket was handed on.\n" + body
        from app.eyewitness.connectors.zammad import ZammadConnector
        connector, _reason = ZammadConnector.for_tenant(rec.tenant_uuid)
        if connector is None:
            return False
        connector.post_internal_note(rec.external_id, body)
        return True
    except Exception:
        logger.exception("eyewitness: follow-up post failed")
        return False


def sweep_timeouts() -> dict:
    """Expire open requests past their link life and release every run
    held past the safety timeout. Called from the beat task."""
    from app.models import db
    from app.eyewitness.models import (RUN_EVIDENCE_PENDING,
                                       EvidenceRequest, IntakeRun)
    now = int(time.time())
    expired = 0
    rows = (EvidenceRequest.query.filter_by(status="open")
            .filter(EvidenceRequest.expires_at < now).all())
    for row in rows:
        row.status = "expired"
        expired += 1
    db.session.commit()
    released = 0
    for row in rows:
        if row.intake_run_id and maybe_release_for_request(row.id):
            released += 1
    stale = (IntakeRun.query.filter_by(status=RUN_EVIDENCE_PENDING)
             .filter(IntakeRun.created_at < now - max_hold_seconds())
             .all())
    for run in stale:
        if release_run(run.id):
            released += 1
    return {"expired": expired, "released": released}

"""Eyewitness routes (M4, M5, S5-manual).

Public: /eyewitness/<token>/* — token-scoped, no login, rate limited.
Technician: /helpdesk/tickets/<id>/evidence/* — login + ticket access.
Captures live under EYEWITNESS_STORAGE_DIR, never app/static, and are
served only through these routes.
"""
from __future__ import annotations

import hashlib
import io
import os
import time
import uuid as uuid_mod

from flask import (Blueprint, abort, jsonify, render_template, request,
                   send_file, session, url_for)

from app.extensions import limiter
from app.models import db

eyewitness_bp = Blueprint("eyewitness_bp", __name__)

LIMITS = {"photo": 25 * 1024 * 1024, "video": 80 * 1024 * 1024,
          "voice": 10 * 1024 * 1024}


def storage_dir() -> str:
    root = os.environ.get("EYEWITNESS_STORAGE_DIR") or os.path.join(
        os.path.dirname(__file__), "..", "..", "data", "eyewitness")
    path = os.path.abspath(root)
    os.makedirs(path, exist_ok=True)
    return path


def _by_token(token: str):
    from app.eyewitness.models import EvidenceRequest
    return EvidenceRequest.query.filter_by(token=token).first()


def _open_or_none(row):
    if row is None or row.status == "cancelled":
        return None
    return row


def _item(row, item_id: str):
    for item in (row.items or []):
        if item.get("id") == item_id:
            return item
    return None


# ── public ────────────────────────────────────────────────────────────

@eyewitness_bp.route("/eyewitness/<token>", methods=["GET"])
def customer_page(token):
    row = _open_or_none(_by_token(token))
    if row is None or row.status not in ("open", "submitted"):
        return render_template("eyewitness/expired.html"), 410
    if int(time.time()) >= int(row.expires_at) and row.status == "open":
        return render_template("eyewitness/expired.html"), 410
    if row.opened_at is None:
        row.opened_at = int(time.time())
        db.session.commit()
    from app.models import Tenants
    tenant = db.session.get(Tenants, row.tenant_uuid)
    msp_name = getattr(tenant, "tenantname", None) or "Your IT support"
    return render_template("eyewitness/customer.html", row=row,
                           msp_name=msp_name, token=token)


@eyewitness_bp.route("/eyewitness/<token>/upload", methods=["POST"])
@limiter.limit("30 per hour")
def upload(token):
    from app.eyewitness.models import Capture
    from app.eyewitness.security import sniff_upload
    row = _open_or_none(_by_token(token))
    if row is None or not row.is_open:
        return jsonify({"error": "link expired"}), 410
    item = _item(row, request.form.get("item_id", ""))
    if item is None:
        return jsonify({"error": "unknown item"}), 400
    recent = (Capture.query.filter_by(request_id=row.id)
              .filter(Capture.created_at > int(time.time()) - 3600).count())
    if recent >= 30:
        return jsonify({"error": "too many uploads"}), 429
    upload_file = request.files.get("file")
    if upload_file is None:
        return jsonify({"error": "no file"}), 400
    data = upload_file.read(LIMITS.get(item["kind"], 25 * 1024 * 1024) + 1)
    if len(data) > LIMITS.get(item["kind"], 25 * 1024 * 1024):
        return jsonify({"error": "file too large"}), 413
    sniff = sniff_upload(data, item["kind"])
    if not sniff["ok"]:
        return jsonify({"error": sniff["reason"]}), 400
    name = f"{uuid_mod.uuid4().hex}.bin"
    path = os.path.join(storage_dir(), name)
    with open(path, "wb") as fh:
        fh.write(data)
    cap = Capture(request_id=row.id, item_id=item["id"], kind=item["kind"],
                  file_path=path, mime=sniff["mime"], size=len(data),
                  sha256=hashlib.sha256(data).hexdigest(),
                  status="uploaded")
    db.session.add(cap)
    db.session.commit()
    try:
        from app.eyewitness.tasks import read_capture
        read_capture.delay(cap.id)
        status = "processing"
    except Exception:  # no broker (dev/test): read inline, same code path
        from app.eyewitness.tasks import process_capture
        status = process_capture(cap.id)["status"]
    return jsonify({"capture_id": cap.id, "status": status}), 201


@eyewitness_bp.route("/eyewitness/<token>/capture/<int:capture_id>", methods=["GET"])
def capture_status(token, capture_id):
    from app.eyewitness.models import Capture
    row = _by_token(token)
    cap = db.session.get(Capture, capture_id)
    if row is None or cap is None or cap.request_id != row.id:
        return jsonify({"error": "not found"}), 404
    advice = (cap.retake or {}).get("advice") if cap.retake else None
    return jsonify({"status": cap.status, "advice": advice,
                    "injection_flag": bool(cap.injection_flag)})


@eyewitness_bp.route("/eyewitness/<token>/skip", methods=["POST"])
def skip(token):
    from app.eyewitness.models import Capture
    row = _open_or_none(_by_token(token))
    if row is None or not row.is_open:
        return jsonify({"error": "link expired"}), 410
    payload = request.get_json(silent=True) or {}
    item = _item(row, str(payload.get("item_id") or ""))
    if item is None:
        return jsonify({"error": "unknown item"}), 400
    cap = Capture(request_id=row.id, item_id=item["id"], kind=item["kind"],
                  status="skipped",
                  skip_reason=str(payload.get("reason") or "")[:200])
    db.session.add(cap)
    db.session.commit()
    return jsonify({"skipped": item["id"]})


@eyewitness_bp.route("/eyewitness/<token>/answer", methods=["POST"])
def answer(token):
    from app.eyewitness.models import Capture
    row = _open_or_none(_by_token(token))
    if row is None or not row.is_open:
        return jsonify({"error": "link expired"}), 410
    payload = request.get_json(silent=True) or {}
    cap = Capture(request_id=row.id,
                  item_id=str(payload.get("item_id") or "")[:64],
                  kind="answer", status="ok",
                  reading={"answer": str(payload.get("answer") or "")[:500]})
    db.session.add(cap)
    db.session.commit()
    return jsonify({"saved": True})


@eyewitness_bp.route("/eyewitness/<token>/voice-offer", methods=["POST"])
def voice_offer(token):
    row = _open_or_none(_by_token(token))
    if row is None:
        return jsonify({"error": "link expired"}), 410
    payload = request.get_json(silent=True) or {}
    row.voice_offer = "accepted" if payload.get("accept") else "declined"
    db.session.commit()
    return jsonify({"voice_offer": row.voice_offer})


@eyewitness_bp.route("/eyewitness/<token>/done", methods=["POST"])
def done(token):
    row = _open_or_none(_by_token(token))
    if row is None:
        return jsonify({"error": "link expired"}), 410
    # "Next time it happens" items keep the link open past Done (7.6).
    has_next_time = any(i.get("open_until") for i in (row.items or []))
    if not has_next_time:
        row.status = "submitted"
        row.submitted_at = int(time.time())
    db.session.commit()
    return jsonify({"done": True, "stays_open": has_next_time})


@eyewitness_bp.route("/eyewitness/<token>/file/<int:capture_id>", methods=["GET"])
def own_file(token, capture_id):
    from app.eyewitness.models import Capture
    row = _by_token(token)
    cap = db.session.get(Capture, capture_id)
    if row is None or cap is None or cap.request_id != row.id or not cap.file_path:
        abort(404)
    return send_file(cap.file_path, mimetype=cap.mime or "application/octet-stream")


# ── technician ────────────────────────────────────────────────────────

def _ticket_or_404(ticket_id: int):
    from app.models.helpdesk import HelpdeskTicket
    from app.utilities.helpdesk_access import may_see
    rec = db.session.get(HelpdeskTicket, ticket_id)
    if rec is None or not may_see(rec):
        abort(404)
    return rec


@eyewitness_bp.route("/helpdesk/tickets/<int:ticket_id>/evidence", methods=["GET"])
def panel(ticket_id):
    from app.utilities.app_access_login_required import login_required

    @login_required
    def _view():
        from app.eyewitness.models import EvidenceRequest, MetaCall
        rec = _ticket_or_404(ticket_id)
        reqs = (EvidenceRequest.query.filter_by(helpdesk_ticket_id=rec.id)
                .order_by(EvidenceRequest.created_at.desc()).all())
        from app.eyewitness.models import Capture
        req_ids = [r.id for r in reqs] or [-1]
        cost = float(db.session.query(db.func.coalesce(db.func.sum(MetaCall.usd), 0))
                     .join(Capture, Capture.id == MetaCall.capture_id)
                     .filter(Capture.request_id.in_(req_ids)).scalar() or 0)
        base = (request.url_root or "").rstrip("/")
        qr = {}
        try:
            import base64
            import qrcode as qr_lib
            for r in reqs:
                buf = io.BytesIO()
                qr_lib.make(f"{base}/eyewitness/{r.token}").save(buf, format="PNG")
                qr[r.token] = ("data:image/png;base64,"
                               + base64.b64encode(buf.getvalue()).decode())
        except Exception:
            qr = {}
        return render_template("eyewitness/panel.html", rec=rec, reqs=reqs,
                               meta_usd=cost, base=base, qr=qr)
    return _view()


@eyewitness_bp.route("/helpdesk/tickets/<int:ticket_id>/evidence/request",
                     methods=["POST"])
def manual_request(ticket_id):
    from app.utilities.app_access_login_required import login_required

    @login_required
    def _view():
        from app.eyewitness import requests as ew_requests
        from app.eyewitness.models import (REQ_NEEDS_APPROVAL, REQ_OPEN,
                                           EvidenceRequest, TenantSettings)
        from app.eyewitness.security import outgoing_filter
        rec = _ticket_or_404(ticket_id)
        templates = request.form.getlist("templates")
        if not templates:
            return jsonify({"error": "choose at least one item"}), 400
        try:
            items = [ew_requests.make_item(t) for t in templates]
        except (KeyError, ValueError):
            return jsonify({"error": "unknown template"}), 400
        settings = db.session.get(TenantSettings, rec.tenant_uuid)
        existing = EvidenceRequest.query.filter_by(
            helpdesk_ticket_id=rec.id).count()
        approval = ew_requests.needs_approval(
            items, existing_requests=existing,
            auto_send_templates=bool(settings and settings.auto_send_templates))
        token = ew_requests.new_token()
        link = url_for("eyewitness_bp.customer_page", token=token,
                       _external=True)
        expires = int(time.time()) + ew_requests.link_ttl_seconds(
            next_time=any(i.get("open_until") for i in items))
        text = ew_requests.render_customer_text(items, link, expires)
        hits = outgoing_filter(text, allowed_url=link)
        row = EvidenceRequest(
            tenant_uuid=rec.tenant_uuid, helpdesk_ticket_id=rec.id,
            token=token, items=items, customer_text=text,
            reason_for_technician="Manual request from the ticket view",
            language="en", status=REQ_NEEDS_APPROVAL if approval else REQ_OPEN,
            filter_hits=hits or None, expires_at=expires)
        db.session.add(row)
        db.session.commit()
        delivered = None
        if row.status == REQ_OPEN and not hits:
            delivered = _deliver(rec, row)
        return jsonify({"request_id": row.id, "status": row.status,
                        "link": link, "filter_hits": hits,
                        "delivered": delivered}), 201
    return _view()


def _deliver(rec, row) -> dict | None:
    """Post the customer-visible note through the tenant's connector."""
    try:
        from app.eyewitness.connectors.zammad import ZammadConnector
        connector, reason = ZammadConnector.for_tenant(rec.tenant_uuid)
        if connector is None:
            row.delivered_via = {"connector": "zammad", "error": str(reason)}
        else:
            out = connector.post_public_note(rec.external_id, row.customer_text)
            row.delivered_via = {"connector": "zammad", **out}
        db.session.commit()
        return row.delivered_via
    except Exception as exc:  # delivery failure never loses the request
        db.session.rollback()
        row.delivered_via = {"connector": "zammad", "error": str(exc)[:200]}
        db.session.commit()
        return row.delivered_via


@eyewitness_bp.route("/helpdesk/tickets/<int:ticket_id>/evidence/file/<int:capture_id>",
                     methods=["GET"])
def tech_file(ticket_id, capture_id):
    from app.utilities.app_access_login_required import login_required

    @login_required
    def _view():
        from app.eyewitness.models import Capture
        rec = _ticket_or_404(ticket_id)
        cap = db.session.get(Capture, capture_id)
        if cap is None:
            abort(404)
        from app.eyewitness.models import EvidenceRequest
        req = db.session.get(EvidenceRequest, cap.request_id)
        if req is None or req.helpdesk_ticket_id != rec.id or not cap.file_path:
            abort(404)
        return send_file(cap.file_path,
                         mimetype=cap.mime or "application/octet-stream",
                         as_attachment=True)
    return _view()

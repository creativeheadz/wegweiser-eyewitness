"""Eyewitness routes (M4, M5, S5-manual).

Public: /eyewitness/<token>/* - token-scoped, no login, rate limited.
Technician: /helpdesk/tickets/<id>/evidence/* - login + ticket access.
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
    msp_logo_url = None
    logo_path = getattr(tenant, "logo_path", None) if tenant else None
    if logo_path:
        logo_file = logo_path[7:] if logo_path.startswith("static/") else logo_path
        logo_file = logo_file.lstrip("/")
        try:
            msp_logo_url = url_for("static", filename=logo_file)
        except Exception:
            msp_logo_url = None
    try:
        next_time_days = int(os.environ.get("EYEWITNESS_NEXT_TIME_DAYS", "7"))
    except ValueError:
        next_time_days = 7
    # Guide photos (S2) ride on their item as a token-scoped file URL.
    guides = {}
    for item in (row.items or []):
        guide_id = item.get("guide_capture_id")
        if guide_id:
            guides[item.get("id")] = url_for(
                "eyewitness_bp.own_file", token=token,
                capture_id=int(guide_id))
    # Each item's state for a reload: finished when its latest
    # capture is ok, or it was skipped or answered. The template
    # marks finished items and the stepper starts at the first
    # unfinished one, so accepting the voice offer lands on the
    # new voice step.
    from app.eyewitness.intake import item_done_states
    from app.eyewitness.models import Capture
    caps = (Capture.query.filter_by(request_id=row.id)
            .order_by(Capture.created_at.asc(), Capture.id.asc()).all())
    item_states = item_done_states(row.items or [], caps)
    from app.eyewitness.meta.client import tenant_reader_backend
    reader_backend = tenant_reader_backend(row.tenant_uuid)
    # A local read takes a minute or two. The page waits longer
    # before it gives up, and says where the check happens.
    read_wait = 600 if reader_backend == "local" else 300
    return render_template("eyewitness/customer.html", row=row,
                           guides=guides, item_states=item_states,
                           msp_name=msp_name, msp_logo_url=msp_logo_url,
                           next_time_days=next_time_days, token=token,
                           reader_backend=reader_backend,
                           read_wait=read_wait)


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
@limiter.limit("30 per hour")
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
    try:
        from app.eyewitness.intake import maybe_release_for_request
        maybe_release_for_request(row.id)
    except Exception:
        pass
    return jsonify({"skipped": item["id"]})


@eyewitness_bp.route("/eyewitness/<token>/answer", methods=["POST"])
@limiter.limit("30 per hour")
def answer(token):
    from app.eyewitness.models import Capture
    row = _open_or_none(_by_token(token))
    if row is None or not row.is_open:
        return jsonify({"error": "link expired"}), 410
    payload = request.get_json(silent=True) or {}
    item = _item(row, str(payload.get("item_id") or ""))
    if item is None:
        return jsonify({"error": "unknown item"}), 400
    cap = Capture(request_id=row.id, item_id=item["id"],
                  kind="answer", status="ok",
                  reading={"answer": str(payload.get("answer") or "")[:500]})
    db.session.add(cap)
    db.session.commit()
    try:
        from app.eyewitness.intake import maybe_release_for_request
        maybe_release_for_request(row.id)
    except Exception:
        pass
    return jsonify({"saved": True})


@eyewitness_bp.route("/eyewitness/<token>/voice-offer", methods=["POST"])
@limiter.limit("30 per hour")
def voice_offer(token):
    row = _open_or_none(_by_token(token))
    if row is None:
        return jsonify({"error": "link expired"}), 410
    payload = request.get_json(silent=True) or {}
    accepted = bool(payload.get("accept"))
    row.voice_offer = "accepted" if accepted else "declined"
    if accepted and not any(i.get("template") == "voice_note"
                             for i in (row.items or [])):
        from app.eyewitness import requests as ew_requests
        row.items = list(row.items or []) + [
            ew_requests.make_item("voice_note")]
    db.session.commit()
    try:
        from app.eyewitness.intake import maybe_release_for_request
        maybe_release_for_request(row.id)
    except Exception:
        pass
    return jsonify({"voice_offer": row.voice_offer})


@eyewitness_bp.route("/eyewitness/<token>/done", methods=["POST"])
@limiter.limit("30 per hour")
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
    try:
        from app.eyewitness.intake import maybe_release_for_request
        maybe_release_for_request(row.id, done=True)
    except Exception:
        pass
    if not has_next_time and getattr(row, "api_key_id", None):
        try:
            from app.eyewitness import callbacks as ew_callbacks
            ew_callbacks.fire(row.id, "evidence.completed")
        except Exception:
            pass
    return jsonify({"done": True, "stays_open": has_next_time})


@eyewitness_bp.route("/get-help/<token>/evidence", methods=["GET"])
@limiter.limit("120 per hour")
def help_evidence(token):
    """The Eyewitness link for one help ticket, and nothing else (S1).

    The help token is the authority, as on the help form. A help
    ticket's request is created in Celery after the send, so the
    sent page polls this endpoint until the link exists. Only a
    request open for the customer is handed out; one that waits for
    a technician stays with the technician.
    """
    from app.models.helpdesk import HelpRequest
    digest = hashlib.sha256((token or "").encode("utf-8")).hexdigest()
    req = HelpRequest.query.filter_by(token_hash=digest).first()
    # The help token's own lifetime bounds this endpoint, so a help
    # address left in a browser history stops handing out the link.
    if req is None or req.status != "submitted" or not req.ticket_id \
            or int(req.expires_at or 0) < int(time.time()):
        return jsonify({"ready": False}), 404
    from app.eyewitness.models import EvidenceRequest
    from app.models.helpdesk import HelpdeskTicket
    record = HelpdeskTicket.query.filter_by(
        tenant_uuid=req.tenant_uuid, provider=req.ticket_provider,
        external_id=str(req.ticket_id)).first()
    if record is None:
        return jsonify({"ready": False})
    row = (EvidenceRequest.query.filter_by(
        helpdesk_ticket_id=record.id, status="open")
        .order_by(EvidenceRequest.created_at.desc()).first())
    if row is None:
        return jsonify({"ready": False})
    from app.utilities.absolute_url import absolute_url
    link = absolute_url(url_for("eyewitness_bp.customer_page",
                                token=row.token))
    qr_data = None
    try:
        import base64
        import qrcode as qr_lib
        buf = io.BytesIO()
        qr_lib.make(link).save(buf, format="PNG")
        qr_data = ("data:image/png;base64,"
                   + base64.b64encode(buf.getvalue()).decode())
    except Exception:
        qr_data = None
    return jsonify({"ready": True, "link": link, "qr": qr_data})


@eyewitness_bp.route("/eyewitness/<token>/file/<int:capture_id>", methods=["GET"])
def own_file(token, capture_id):
    from app.eyewitness.models import Capture
    row = _by_token(token)
    cap = db.session.get(Capture, capture_id)
    if row is None or cap is None or cap.request_id != row.id or not cap.file_path:
        abort(404)
    resp = send_file(cap.file_path,
                     mimetype=cap.mime or "application/octet-stream")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


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
        from app.eyewitness.intake import ticket_meta_usd
        from app.eyewitness.models import EvidenceRequest
        rec = _ticket_or_404(ticket_id)
        reqs = (EvidenceRequest.query.filter_by(helpdesk_ticket_id=rec.id)
                .order_by(EvidenceRequest.created_at.desc()).all())
        cost = ticket_meta_usd(reqs)
        from app.eyewitness.models import Capture
        req_ids = [r.id for r in reqs] or [-1]
        caps = (Capture.query.filter(Capture.request_id.in_(req_ids),
                                     Capture.kind == "photo")
                .order_by(Capture.created_at.desc()).all())
        # The verdict and the state of each item, for the technician:
        # the intake's assessment per run, and the latest capture per
        # request item (rows ordered oldest first, so the last wins).
        from app.eyewitness.models import IntakeRun
        runs = {}
        for r in reqs:
            if r.intake_run_id and r.intake_run_id not in runs:
                runs[r.intake_run_id] = db.session.get(IntakeRun,
                                                       r.intake_run_id)
        latest = {}
        for c in (Capture.query.filter(Capture.request_id.in_(req_ids))
                  .order_by(Capture.created_at.asc(), Capture.id.asc())
                  .all()):
            latest[(c.request_id, c.item_id)] = c
        # Where each reading was made and how long it took, from the
        # call log. The newest call for a capture names its reading.
        from app.eyewitness.models import MetaCall
        from app.eyewitness.summary import read_provenance
        provenance = {}
        cap_ids = [c.id for c in latest.values()]
        if cap_ids:
            for call in (MetaCall.query.filter(
                    MetaCall.capture_id.in_(cap_ids))
                    .order_by(MetaCall.created_at.desc(),
                              MetaCall.id.desc()).all()):
                provenance.setdefault(call.capture_id,
                                      read_provenance(call))
        from app.eyewitness.meta.client import tenant_reader_backend
        reader_backend = tenant_reader_backend(rec.tenant_uuid)
        from app.utilities.absolute_url import absolute_url
        links = {}
        qr = {}
        try:
            import base64
            import qrcode as qr_lib
            for r in reqs:
                link = absolute_url(url_for(
                    "eyewitness_bp.customer_page", token=r.token))
                links[r.token] = link
                buf = io.BytesIO()
                qr_lib.make(link).save(buf, format="PNG")
                qr[r.token] = ("data:image/png;base64,"
                               + base64.b64encode(buf.getvalue()).decode())
        except Exception:
            qr = {}
        return render_template("eyewitness/panel.html", rec=rec, reqs=reqs,
                               caps=caps, runs=runs, latest=latest,
                               meta_usd=cost, links=links, qr=qr,
                               provenance=provenance,
                               reader_backend=reader_backend)
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
        # A technician wrote this request, so they are its approver: the
        # approval gate (brief 7.8) is for what the intake agent proposes.
        # The outgoing filter below still applies to every customer-bound text.
        approval = False
        token = ew_requests.new_token()
        # Wegweiser's rule: links come from APP_BASE_URL, never from the
        # Host header of the request.
        from app.utilities.absolute_url import absolute_url
        link = absolute_url(url_for("eyewitness_bp.customer_page",
                                    token=token))
        expires = int(time.time()) + ew_requests.link_ttl_seconds(
            next_time=any(i.get("open_until") for i in items))
        from app.models import Tenants
        tenant = db.session.get(Tenants, rec.tenant_uuid)
        msp_name = getattr(tenant, "tenantname", None) or "Your IT support"
        text = ew_requests.render_customer_text(items, link, expires,
                                                msp_name=msp_name)
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
            from app.eyewitness import requests as ew_requests
            out = connector.post_public_note(
                rec.external_id, row.customer_text,
                html=ew_requests.render_customer_html(row.customer_text,
                                                      row.items))
            row.delivered_via = {"connector": "zammad", **out}
        db.session.commit()
        return row.delivered_via
    except Exception as exc:  # delivery failure never loses the request
        db.session.rollback()
        row.delivered_via = {"connector": "zammad", "error": str(exc)[:200]}
        db.session.commit()
        return row.delivered_via


@eyewitness_bp.route(
    "/helpdesk/tickets/<int:ticket_id>/evidence/<int:req_id>/approve",
    methods=["POST"])
def approve_request(ticket_id, req_id):
    from app.utilities.app_access_login_required import login_required

    @login_required
    def _view():
        from app.eyewitness.models import REQ_OPEN, EvidenceRequest
        rec = _ticket_or_404(ticket_id)
        row = db.session.get(EvidenceRequest, req_id)
        if row is None or row.helpdesk_ticket_id != rec.id:
            abort(404)
        if row.status != "needs_approval":
            return jsonify({"error": "not waiting for approval"}), 400
        if row.filter_hits:
            return jsonify({"error": "outgoing filter blocked this text",
                            "filter_hits": row.filter_hits}), 400
        row.status = REQ_OPEN
        try:
            from app.utilities.current_user import current_user_uuid
            row.approved_by = current_user_uuid()
        except Exception:
            row.approved_by = None
        row.approved_at = int(time.time())
        db.session.commit()
        delivered = _deliver(rec, row)
        return jsonify({"request_id": row.id, "status": row.status,
                        "delivered": delivered})
    return _view()


@eyewitness_bp.route(
    "/helpdesk/tickets/<int:ticket_id>/evidence/<int:req_id>/cancel",
    methods=["POST"])
def cancel_request(ticket_id, req_id):
    from app.utilities.app_access_login_required import login_required

    @login_required
    def _view():
        from app.eyewitness.models import EvidenceRequest
        rec = _ticket_or_404(ticket_id)
        row = db.session.get(EvidenceRequest, req_id)
        if row is None or row.helpdesk_ticket_id != rec.id:
            abort(404)
        row.status = "cancelled"
        db.session.commit()
        released = False
        try:
            from app.eyewitness.intake import maybe_release_for_request
            released = maybe_release_for_request(row.id, manual=True)
        except Exception:
            pass
        return jsonify({"request_id": row.id, "status": row.status,
                        "released": released})
    return _view()


@eyewitness_bp.route(
    "/helpdesk/tickets/<int:ticket_id>/evidence/<int:req_id>/release",
    methods=["POST"])
def release_now(ticket_id, req_id):
    from app.utilities.app_access_login_required import login_required

    @login_required
    def _view():
        from app.eyewitness.models import EvidenceRequest
        rec = _ticket_or_404(ticket_id)
        row = db.session.get(EvidenceRequest, req_id)
        if row is None or row.helpdesk_ticket_id != rec.id:
            abort(404)
        released = False
        try:
            from app.eyewitness.intake import maybe_release_for_request
            released = maybe_release_for_request(row.id, manual=True)
        except Exception:
            pass
        return jsonify({"request_id": row.id, "released": released})
    return _view()


@eyewitness_bp.route(
    "/helpdesk/tickets/<int:ticket_id>/evidence/guide",
    methods=["POST"])
def make_guide_request(ticket_id):
    """Draw a guide on an earlier photo (S2, technician only).

    The new request always waits for approval, under the second
    request rule, however many requests the ticket already holds.
    Nothing here runs from the intake agent.
    """
    from app.utilities.app_access_login_required import login_required

    @login_required
    def _view():
        from app.eyewitness import requests as ew_requests
        from app.eyewitness.models import (REQ_NEEDS_APPROVAL, Capture,
                                           EvidenceRequest)
        from app.eyewitness.security import outgoing_filter
        rec = _ticket_or_404(ticket_id)
        try:
            source_id = int(request.form.get("capture_id") or 0)
        except ValueError:
            return jsonify({"error": "choose a photo"}), 400
        source = db.session.get(Capture, source_id)
        if source is None or not source.file_path:
            return jsonify({"error": "choose a photo"}), 400
        source_req = db.session.get(EvidenceRequest,
                                    source.request_id)
        if source_req is None \
                or source_req.helpdesk_ticket_id != rec.id:
            abort(404)
        with open(source.file_path, "rb") as fh:
            photo = fh.read()
        style = request.form.get("style") or "circle"
        client = None
        try:
            from app.eyewitness.meta.client import reader_client
            client = reader_client(rec.tenant_uuid)
        except Exception:
            client = None
        from app.eyewitness.readers.guide import make_guide
        made = make_guide(photo, style, client=client,
                          capture_id=source.id)
        name = f"{uuid_mod.uuid4().hex}.bin"
        path = os.path.join(storage_dir(), name)
        with open(path, "wb") as fh:
            fh.write(made["bytes"])
        token = ew_requests.new_token()
        from app.utilities.absolute_url import absolute_url
        link = absolute_url(url_for("eyewitness_bp.customer_page",
                                    token=token))
        expires = int(time.time()) + ew_requests.link_ttl_seconds()
        items = [{"id": "guide_photo", "template": "custom",
                  "kind": "photo", "title": "Take this photo again",
                  "instruction": "Your support team marked one of "
                                 "your photos. Take a new photo of "
                                 "the marked part.",
                  "max_seconds": None, "custom": True,
                  "open_until": None, "guide_capture_id": None}]
        from app.models import Tenants as _Tenants
        _tenant = db.session.get(_Tenants, rec.tenant_uuid)
        _msp = getattr(_tenant, "tenantname", None) or "Your IT support"
        text = ew_requests.render_customer_text(items, link, expires,
                                                msp_name=_msp)
        hits = outgoing_filter(text, allowed_url=link)
        row = EvidenceRequest(
            tenant_uuid=rec.tenant_uuid, helpdesk_ticket_id=rec.id,
            token=token, items=items, customer_text=text,
            reason_for_technician="Guide photo drawn by a "
                                  "technician",
            language="en", status=REQ_NEEDS_APPROVAL,
            filter_hits=hits or None, expires_at=expires)
        db.session.add(row)
        db.session.flush()
        guide = Capture(
            request_id=row.id, item_id="guide", kind="photo",
            file_path=path, mime="image/jpeg",
            size=len(made["bytes"]),
            sha256=hashlib.sha256(made["bytes"]).hexdigest(),
            status="ok",
            reading={"summary": "guide photo",
                     "method": made["method"], "style": style})
        db.session.add(guide)
        db.session.flush()
        items[0]["guide_capture_id"] = guide.id
        row.items = items
        db.session.commit()
        return jsonify({"request_id": row.id, "status": row.status,
                        "guide_capture_id": guide.id,
                        "method": made["method"]}), 201
    return _view()


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
        resp = send_file(cap.file_path,
                         mimetype=cap.mime or "application/octet-stream",
                         as_attachment=True)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp
    return _view()

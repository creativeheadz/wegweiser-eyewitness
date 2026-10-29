"""Phase 5 routes: Zammad push webhook, Evidence API v1, settings keys.

The webhook is signed and tokenless, so it sits apart from the
session routes in routes.py. The API is keyed per tenant, and a key
sees its own tenant alone. Customer text in every response comes
from the template renderer, and readings travel fenced as data.
"""
from __future__ import annotations

import functools
import json
import time

from flask import Blueprint, abort, jsonify, request, url_for

from app.extensions import limiter
from app.models import db

eyewitness_api_bp = Blueprint("eyewitness_api_bp", __name__)


def ingest_ticket(tenant_uuid, external_id):
    """Indirection so tests can stand in for the desk fetch."""
    from app.utilities.helpdesk_inbound import ingest_ticket as real
    return real(tenant_uuid, external_id)


# ── Zammad push webhook ─────────────────────────────────────────────

@eyewitness_api_bp.route("/eyewitness/hooks/zammad/<hook_id>", methods=["POST"])
@limiter.limit("120 per minute")
def zammad_hook(hook_id):
    from app.eyewitness import webhook as ew_hook
    from app.eyewitness.models import TenantSettings
    from app.utilities.secret_manager import get_secret

    if (request.content_length or 0) > ew_hook.MAX_WEBHOOK_BODY:
        return jsonify({"error": "body too large"}), 413
    raw = request.get_data(cache=True) or b""
    if len(raw) > ew_hook.MAX_WEBHOOK_BODY:
        return jsonify({"error": "body too large"}), 413
    settings = TenantSettings.query.filter_by(
        webhook_hook_id=hook_id).first()
    if settings is None:
        return jsonify({"error": "unknown hook"}), 404
    secret = get_secret(ew_hook.webhook_secret_name(settings.tenant_uuid))
    header = request.headers.get("X-Hub-Signature")
    if not ew_hook.verify_zammad_signature(raw, header, secret):
        return jsonify({"error": "bad signature"}), 401
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except (ValueError, UnicodeDecodeError):
        return jsonify({"error": "bad json"}), 400
    external_id = ew_hook.extract_ticket_id(payload)
    if external_id is None:
        return jsonify({"error": "no ticket id"}), 400
    result = ingest_ticket(settings.tenant_uuid, external_id)
    if not result.get("ok"):
        return jsonify({"error": result.get("reason") or "ingest failed"}), 502
    return jsonify({"ok": True, "created": bool(result.get("created")),
                    "duplicate": bool(result.get("duplicate")),
                    "held": bool(result.get("held")),
                    "id": result.get("id")}), 200


# ── Evidence API key auth ───────────────────────────────────────────

def _presented_key() -> str:
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.headers.get("X-API-Key") or "").strip()


def _find_key(plaintext: str):
    from app.eyewitness import api as ew_api
    from app.eyewitness.models import ApiKey
    if not plaintext:
        return None
    row = ApiKey.query.filter_by(
        key_prefix=ew_api.prefix_of(plaintext)).first()
    if row is None or row.revoked_at:
        return None
    if not ew_api.key_matches(plaintext, row.key_hash):
        return None
    return row


def require_api_key(scope: str):
    def decorator(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            from app.eyewitness import api as ew_api
            row = _find_key(_presented_key())
            if row is None:
                return jsonify({"error": "invalid api key"}), 401
            if scope and not ew_api.has_scope(row, scope):
                return jsonify({"error": "key lacks this scope"}), 403
            row.last_used_at = int(time.time())
            db.session.commit()
            request.eyewitness_api_key = row
            return view(*args, **kwargs)
        return wrapped
    return decorator


def _api_key():
    return getattr(request, "eyewitness_api_key", None)


def _own_request_or_404(request_id):
    from app.eyewitness import api as ew_api
    from app.eyewitness.models import EvidenceRequest
    row = db.session.get(EvidenceRequest, int(request_id))
    if row is None or not ew_api.visible_to_tenant(row, _api_key().tenant_uuid):
        abort(404)
    return row


# ── Evidence API v1 ─────────────────────────────────────────────────

@eyewitness_api_bp.route("/api/eyewitness/v1/templates", methods=["GET"])
@limiter.limit("60 per minute")
@require_api_key("")
def api_templates():
    from app.eyewitness import api as ew_api
    return jsonify(ew_api.templates_payload())


@eyewitness_api_bp.route("/api/eyewitness/v1/requests", methods=["POST"])
@limiter.limit("30 per minute")
@require_api_key("evidence:write")
def api_create_request():
    from app.eyewitness import api as ew_api
    from app.eyewitness import requests as ew_requests
    from app.eyewitness.models import EvidenceRequest
    from app.eyewitness.security import outgoing_filter

    key_row = _api_key()
    # A request body is a few fields and a ticket text: 64 KB is plenty.
    if (request.content_length or 0) > 64 * 1024:
        return jsonify({"error": "body too large"}), 413
    payload = request.get_json(silent=True) or {}
    external_ref = str(payload.get("external_ref") or "")[:200] or None

    if payload.get("auto"):
        ticket_text = str(payload.get("ticket_text") or "")
        if not ticket_text.strip():
            return jsonify({"error": "ticket_text is required"}), 400
        client = None
        try:
            from app.eyewitness.meta.client import reader_client
            client = reader_client(key_row.tenant_uuid)
        except Exception:
            client = None
        decision = ew_api.decide_auto(ticket_text, client=client)
        if not decision["needs_evidence"]:
            return jsonify({"needs_evidence": False,
                            "reason": decision.get("reason") or ""}), 200
        templates = decision["templates"]
        questions = decision["questions"]
        reason = decision.get("reason") or ""
    else:
        raw_items = payload.get("items") or []
        templates = payload.get("templates") or [
            item.get("template") for item in raw_items
            if isinstance(item, dict) and item.get("template")]
        questions = payload.get("questions") or []
        questions = [q.get("template") if isinstance(q, dict) else q
                     for q in questions]
        reason = str(payload.get("reason") or "")[:1000]

    templates = [str(t) for t in (templates or [])]
    questions = [str(q) for q in (questions or [])]
    if not templates and not questions:
        return jsonify({"error": "choose at least one template"}), 400
    if len(templates) > 4 or len(questions) > 2:
        return jsonify({"error": "too many items"}), 400
    try:
        items = [ew_requests.make_item(t) for t in templates]
        items += [ew_requests.make_question_item(q) for q in questions]
    except (KeyError, ValueError):
        return jsonify({"error": "unknown template"}), 400

    token = ew_requests.new_token()
    from app.utilities.absolute_url import absolute_url
    link = absolute_url(url_for("eyewitness_bp.customer_page", token=token))
    expires = int(time.time()) + ew_requests.link_ttl_seconds(
        next_time=any(i.get("open_until") for i in items))
    from app.models import Tenants
    tenant = db.session.get(Tenants, key_row.tenant_uuid)
    msp_name = getattr(tenant, "tenantname", None) or "Your IT support"
    text = ew_requests.render_customer_text(items, link, expires,
                                            msp_name=msp_name)
    hits = outgoing_filter(text, allowed_url=link)
    if hits:
        return jsonify({"error": "outgoing filter blocked this text",
                        "filter_hits": hits}), 400
    row = EvidenceRequest(
        tenant_uuid=key_row.tenant_uuid, token=token, items=items,
        customer_text=text, reason_for_technician=reason or None,
        language="en", status="open", expires_at=expires, source="api",
        external_ref=external_ref, api_key_id=key_row.id)
    db.session.add(row)
    db.session.commit()
    return jsonify({"id": row.id, "status": row.status,
                    "customer_text": text, "link": link,
                    "expires_at": expires, "items": items,
                    "needs_evidence": True}), 201


@eyewitness_api_bp.route("/api/eyewitness/v1/requests/<int:request_id>",
                         methods=["GET"])
@limiter.limit("120 per minute")
@require_api_key("evidence:read")
def api_get_request(request_id):
    from app.eyewitness import api as ew_api
    from app.eyewitness.models import Capture
    row = _own_request_or_404(request_id)
    caps = (Capture.query.filter_by(request_id=row.id)
            .order_by(Capture.created_at.asc(), Capture.id.asc()).all())
    return jsonify(ew_api.serialise_request(row, caps))


@eyewitness_api_bp.route("/api/eyewitness/v1/requests/<int:request_id>/release",
                         methods=["POST"])
@limiter.limit("30 per minute")
@require_api_key("evidence:write")
def api_release_request(request_id):
    row = _own_request_or_404(request_id)
    released = False
    if row.intake_run_id:
        try:
            from app.eyewitness.intake import maybe_release_for_request
            released = maybe_release_for_request(row.id, manual=True)
        except Exception:
            released = False
    elif row.status == "open":
        row.status = "submitted"
        row.submitted_at = int(time.time())
        db.session.commit()
        released = True
    if released:
        try:
            from app.eyewitness import callbacks as ew_callbacks
            ew_callbacks.fire(row.id, "evidence.completed")
        except Exception:
            pass
    return jsonify({"id": row.id, "released": released,
                    "status": row.status})


@eyewitness_api_bp.route("/api/eyewitness/v1/requests/<int:request_id>/cancel",
                         methods=["POST"])
@limiter.limit("30 per minute")
@require_api_key("evidence:write")
def api_cancel_request(request_id):
    row = _own_request_or_404(request_id)
    row.status = "cancelled"
    db.session.commit()
    released = False
    if row.intake_run_id:
        try:
            from app.eyewitness.intake import maybe_release_for_request
            released = maybe_release_for_request(row.id, manual=True)
        except Exception:
            released = False
    return jsonify({"id": row.id, "status": row.status,
                    "released": released})


# ── technician settings (session auth, for the settings page) ───────

@eyewitness_api_bp.route("/eyewitness/settings.json", methods=["GET"])
def settings_data():
    from app.utilities.app_access_login_required import login_required

    @login_required
    def _view():
        from flask import session
        from app.eyewitness import webhook as ew_hook
        from app.eyewitness.models import ApiKey
        tenant_uuid = session.get("tenant_uuid")
        if not tenant_uuid:
            return jsonify({"error": "no tenant in session"}), 400
        import uuid as uuid_mod
        try:
            tenant = uuid_mod.UUID(str(tenant_uuid))
        except ValueError:
            return jsonify({"error": "bad tenant"}), 400
        keys = (ApiKey.query.filter_by(tenant_uuid=tenant)
                .order_by(ApiKey.created_at.desc()).all())
        from app.eyewitness.meta.client import tenant_reader_backend
        from app.eyewitness.meta.local import local_base_url, local_model
        return jsonify({
            "hook_url": ew_hook.hook_url_for(tenant),
            "hook_configured": bool(ew_hook.hook_url_for(tenant)),
            "reader_backend": tenant_reader_backend(tenant),
            "local_available": bool(local_base_url()),
            "local_model": local_model(),
            "api_keys": [
                {"id": k.id, "name": k.name, "prefix": k.key_prefix,
                 "scopes": k.scopes or [], "callback_url": k.callback_url,
                 "last_used_at": k.last_used_at,
                 "revoked_at": k.revoked_at,
                 "created_at": k.created_at} for k in keys],
        })
    return _view()


@eyewitness_api_bp.route("/eyewitness/settings/reader-backend",
                         methods=["POST"])
def settings_reader_backend():
    from app.utilities.app_access_login_required import login_required

    @login_required
    def _view():
        from flask import session
        from app.eyewitness.meta.local import local_base_url
        from app.eyewitness.models import TenantSettings
        tenant_raw = session.get("tenant_uuid")
        if not tenant_raw:
            return jsonify({"error": "no tenant in session"}), 400
        import uuid as uuid_mod
        try:
            tenant = uuid_mod.UUID(str(tenant_raw))
        except ValueError:
            return jsonify({"error": "bad tenant"}), 400
        backend = str(request.form.get("reader_backend") or "meta")
        if backend not in ("meta", "local"):
            return jsonify({"error": "choose where readings happen"}), 400
        if backend == "local" and not local_base_url():
            return jsonify({"error": "no local server is set up on "
                                     "this installation"}), 400
        row = db.session.get(TenantSettings, tenant)
        if row is None:
            row = TenantSettings(tenant_uuid=tenant, enabled=False,
                                 reader_backend=backend)
            db.session.add(row)
        else:
            row.reader_backend = backend
        db.session.commit()
        return jsonify({"reader_backend": backend})
    return _view()


@eyewitness_api_bp.route("/eyewitness/settings/api-keys", methods=["POST"])
def settings_create_key():
    from app.utilities.app_access_login_required import login_required

    @login_required
    def _view():
        from flask import session
        from app.eyewitness import api as ew_api
        from app.eyewitness.models import ApiKey
        tenant_raw = session.get("tenant_uuid")
        if not tenant_raw:
            return jsonify({"error": "no tenant in session"}), 400
        import uuid as uuid_mod
        try:
            tenant = uuid_mod.UUID(str(tenant_raw))
        except ValueError:
            return jsonify({"error": "bad tenant"}), 400
        name = str(request.form.get("name") or "Evidence API")[:120]
        scopes = [s for s in request.form.getlist("scopes")
                  if s in ew_api.SCOPES]
        if not scopes:
            scopes = ["evidence:write", "evidence:read"]
        callback_url = (request.form.get("callback_url") or "").strip() or None
        if callback_url:
            from app.utilities.ssrf_guard import SSRFError, assert_safe_url
            try:
                assert_safe_url(callback_url, block_private=True)
            except SSRFError as exc:
                return jsonify({"error": f"callback address refused: {exc}"}), 400
        plaintext, prefix, hashed = ew_api.generate_api_key()
        row = ApiKey(tenant_uuid=tenant, name=name, key_prefix=prefix,
                     key_hash=hashed, scopes=scopes,
                     callback_url=callback_url)
        try:
            from app.utilities.current_user import current_user_uuid
            row.created_by = current_user_uuid()
        except Exception:
            row.created_by = None
        db.session.add(row)
        db.session.flush()
        callback_secret = None
        if callback_url:
            import secrets as secrets_mod
            callback_secret = secrets_mod.token_urlsafe(32)
            from app.utilities.secret_manager import set_secret
            from app.eyewitness.webhook import callback_secret_name
            set_secret(callback_secret_name(row.id), callback_secret)
        db.session.commit()
        return jsonify({"id": row.id, "name": row.name,
                        "key": plaintext, "prefix": prefix,
                        "scopes": scopes, "callback_url": callback_url,
                        "callback_secret": callback_secret}), 201
    return _view()


@eyewitness_api_bp.route("/eyewitness/settings/api-keys/<int:key_id>/revoke",
                         methods=["POST"])
def settings_revoke_key(key_id):
    from app.utilities.app_access_login_required import login_required

    @login_required
    def _view(key_id):
        from flask import session
        from app.eyewitness.models import ApiKey
        tenant_raw = session.get("tenant_uuid")
        row = db.session.get(ApiKey, int(key_id))
        if row is None or str(row.tenant_uuid) != str(tenant_raw):
            abort(404)
        row.revoked_at = int(time.time())
        db.session.commit()
        return jsonify({"id": row.id, "revoked_at": row.revoked_at})
    return _view(key_id)

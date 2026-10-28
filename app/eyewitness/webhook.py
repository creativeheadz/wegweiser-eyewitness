"""Zammad push webhook identity, signature checks and setup (Phase 5).

One hook id per tenant is the public half of the push address. The
HMAC secret sits in the secret store under
``eyewitness_webhook_secret:<tenant_uuid>``, with the same
get_secret and set_secret calls that hold the Zammad API token for
``TenantIntegration``. The secret never sits in a table and never
prints. The five minute poll stays as the fallback for a push that
never arrives.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets

logger = logging.getLogger(__name__)

MAX_WEBHOOK_BODY = 64 * 1024


def webhook_secret_name(tenant_uuid) -> str:
    return f"eyewitness_webhook_secret:{tenant_uuid}"


def callback_secret_name(api_key_id) -> str:
    return f"eyewitness_callback_secret:{api_key_id}"


def compute_zammad_signature(raw_body: bytes, secret: str) -> str:
    """The hex digest Zammad puts after ``sha1=`` in X-Hub-Signature."""
    return hmac.new(secret.encode("utf-8"), raw_body,
                    hashlib.sha1).hexdigest()


def verify_zammad_signature(raw_body: bytes, header: str | None,
                            secret: str | None) -> bool:
    """Constant time check of ``sha1=<hex>`` over the raw body."""
    if not secret or not header:
        return False
    value = header.strip()
    if value.startswith("sha1="):
        value = value[5:]
    expected = compute_zammad_signature(raw_body, secret)
    return hmac.compare_digest(expected, value.strip().lower())


def extract_ticket_id(payload) -> str | None:
    """Ticket id from a Zammad webhook payload, in its known shapes.

    The default payload nests the ticket. A custom payload may send
    the id at the top level or under ticket_id. Anything else is not
    a ticket push this handler can index.
    """
    if not isinstance(payload, dict):
        return None
    ticket = payload.get("ticket")
    if isinstance(ticket, dict) and ticket.get("id") is not None:
        return str(ticket["id"])
    for key in ("ticket_id", "ticketId", "id"):
        if payload.get(key) is not None:
            return str(payload[key])
    return None


def ensure_hook(tenant_uuid):
    """Hook id and secret for one tenant, creating both where missing.

    Returns (hook_id, created). The hook id is stored on the tenant
    settings row. The secret is stored only through the secret store.
    """
    from app.models import db
    from app.eyewitness.models import TenantSettings
    from app.utilities.secret_manager import get_secret, set_secret

    settings = db.session.get(TenantSettings, tenant_uuid)
    if settings is None:
        settings = TenantSettings(tenant_uuid=tenant_uuid, enabled=False)
        db.session.add(settings)
        db.session.flush()
    created = False
    if not settings.webhook_hook_id:
        settings.webhook_hook_id = secrets.token_urlsafe(24)
        created = True
    name = webhook_secret_name(tenant_uuid)
    if not get_secret(name):
        set_secret(name, secrets.token_urlsafe(32))
        created = True
    db.session.commit()
    return settings.webhook_hook_id, created


def hook_path(hook_id: str) -> str:
    return f"/eyewitness/hooks/zammad/{hook_id}"


def hook_url_for(tenant_uuid, base_url: str = "") -> str:
    """Absolute hook address for the settings page and for setup."""
    from app.eyewitness.models import TenantSettings
    from app.models import db
    settings = db.session.get(TenantSettings, tenant_uuid)
    hook_id = settings.webhook_hook_id if settings else None
    if not hook_id:
        return ""
    path = hook_path(hook_id)
    base = (base_url or "").rstrip("/")
    if base:
        return base + path
    try:
        from flask import has_app_context
        if has_app_context():
            from app.utilities.absolute_url import absolute_url
            return absolute_url(path)
    except Exception:
        pass
    import os
    return (os.environ.get("APP_BASE_URL")
            or "https://app.wegweiser.tech").rstrip("/") + path


def _admin_request(method, url, token, payload=None, request_fn=None):
    headers = {"Authorization": f"Token token={token}",
               "Content-Type": "application/json"}
    if request_fn is not None:
        return request_fn(method, url, headers=headers, json=payload)
    from app.utilities.ssrf_guard import guarded_request
    from app.utilities.zammad_client import _private_endpoint_allowed
    block_private = not _private_endpoint_allowed(url)
    return guarded_request(method, url, block_private=block_private,
                           headers=headers, timeout=20,
                           allow_redirects=False,
                           json=payload)


def setup_zammad_webhook(tenant_uuid, *, admin_token: str,
                         base_url: str = "", endpoint: str = "",
                         request_fn=None) -> dict:
    """Create the Zammad webhook and its ticket created trigger.

    The admin token arrives at run time from the caller, goes into
    the two calls below, and is never stored or logged. The webhook
    is created first, then a trigger on ticket creation points at
    its id, with the tenant HMAC secret as the signature token so
    every push carries X-Hub-Signature. An existing webhook for the
    same endpoint is reused, and an existing trigger with the
    Eyewitness name is left in place.
    """
    if not admin_token:
        raise ValueError("an admin token is required at run time")
    from app.models.tenant_integration import TenantIntegration
    from app.utilities.secret_manager import get_secret
    from app.utilities.zammad_client import ZammadError, normalize_endpoint

    hook_id, _created = ensure_hook(tenant_uuid)
    path = hook_path(hook_id)
    if base_url:
        hook_url = base_url.rstrip("/") + path
    else:
        hook_url = hook_url_for(tenant_uuid, base_url)
    secret = get_secret(webhook_secret_name(tenant_uuid))
    if not secret:
        raise ValueError("webhook secret missing after setup")

    if endpoint:
        api_base = normalize_endpoint(endpoint)
    else:
        row = TenantIntegration.query.filter_by(
            tenant_uuid=tenant_uuid, provider="zammad").first()
        if row is None or not row.endpoint:
            raise ValueError("no Zammad endpoint stored for this tenant")
        api_base = normalize_endpoint(row.endpoint)

    def call(method, suffix, payload=None):
        resp = _admin_request(method, f"{api_base}{suffix}", admin_token,
                              payload=payload, request_fn=request_fn)
        status = getattr(resp, "status_code", 0)
        try:
            body = resp.json()
        except Exception:
            body = None
        if status and not (200 <= status < 300):
            raise ZammadError(f"Zammad returned {status} for {suffix}")
        return body

    existing = call("GET", "/webhooks")
    webhook_id = None
    if isinstance(existing, list):
        for item in existing:
            if isinstance(item, dict) and item.get("endpoint") == hook_url:
                webhook_id = item.get("id")
                break
    if webhook_id is None:
        made = call("POST", "/webhooks", {
            "name": "Eyewitness ticket created",
            "endpoint": hook_url,
            "http_method": "post",
            "signature_token": secret,
            "ssl_verify": True,
            "customized_payload": False,
            "active": True,
        })
        webhook_id = made.get("id") if isinstance(made, dict) else None
        if webhook_id is None:
            raise ZammadError("Zammad webhook creation gave no id")

    triggers = call("GET", "/triggers")
    trigger_id = None
    if isinstance(triggers, list):
        for item in triggers:
            if isinstance(item, dict) and item.get("name") == "Eyewitness ticket created":
                trigger_id = item.get("id")
                break
    if trigger_id is None:
        made_trigger = call("POST", "/triggers", {
            "name": "Eyewitness ticket created",
            "active": True,
            "condition": {"ticket.action": {"operator": "is",
                                            "value": "create"}},
            "perform": {"notification.webhook": {"webhook_id": webhook_id}},
        })
        trigger_id = (made_trigger.get("id")
                      if isinstance(made_trigger, dict) else None)
        if trigger_id is None:
            raise ZammadError("Zammad trigger creation gave no id")
    return {"hook_url": hook_url, "webhook_id": webhook_id,
            "trigger_id": trigger_id}

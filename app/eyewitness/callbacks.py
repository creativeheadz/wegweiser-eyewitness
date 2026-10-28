"""Signed Evidence API callbacks with retry (Phase 5).

Every callback address passes the SSRF guard where it connects, with
the same checked connection the Zammad client uses. Delivery runs
in Celery and retries with growing waits. A failed address stops
after the bounded attempts in tasks.py and stays visible on its
delivery row.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


def enqueue_callback(request_row, event: str, capture_id=None):
    """Queue one signed callback for one API request, or return None.

    Requests with no API key, or a key with no callback address, have
    nowhere to send. The delivery row is the record either way a
    queue failure happens, and the Celery call follows its commit.
    """
    from app.models import db
    from app.eyewitness import api as ew_api
    from app.eyewitness.models import ApiKey, CallbackDelivery

    if request_row is None or not getattr(request_row, "api_key_id", None):
        return None
    key_row = db.session.get(ApiKey, request_row.api_key_id)
    if key_row is None or not key_row.callback_url or key_row.revoked_at:
        return None
    payload = ew_api.callback_payload(event, request_row,
                                      capture_id=capture_id)
    delivery = CallbackDelivery(api_key_id=key_row.id,
                                request_id=request_row.id,
                                event=event, payload=payload,
                                status="pending")
    db.session.add(delivery)
    db.session.commit()
    try:
        from app.eyewitness.tasks import deliver_callback
        deliver_callback.delay(delivery.id)
    except Exception:
        logger.warning("eyewitness: callback %s queued for retry by beat",
                       delivery.id)
    return delivery


def fire(request_id, event: str, capture_id=None) -> bool:
    """Best effort event from a capture, release or expiry path."""
    try:
        from app.models import db
        from app.eyewitness.models import EvidenceRequest
        row = db.session.get(EvidenceRequest, int(request_id))
        return enqueue_callback(row, event, capture_id=capture_id) is not None
    except Exception:
        logger.exception("eyewitness: callback fire failed for %s", request_id)
        return False


def deliver(delivery_id: int, request_fn=None) -> dict:
    """One delivery attempt. Raises on failure so Celery can retry."""
    from app.models import db
    from app.eyewitness import api as ew_api
    from app.eyewitness.models import ApiKey, CallbackDelivery
    from app.eyewitness.webhook import callback_secret_name
    from app.utilities.secret_manager import get_secret

    delivery = db.session.get(CallbackDelivery, int(delivery_id))
    if delivery is None:
        return {"missing": True}
    if delivery.status == "delivered":
        return {"delivered": True, "already": True}
    key_row = db.session.get(ApiKey, delivery.api_key_id)
    if key_row is None or not key_row.callback_url:
        delivery.status = "failed"
        delivery.last_error = "callback address gone"
        db.session.commit()
        return {"failed": "callback address gone"}
    secret = get_secret(callback_secret_name(key_row.id))
    if not secret:
        delivery.status = "failed"
        delivery.last_error = "callback secret missing"
        db.session.commit()
        return {"failed": "callback secret missing"}

    body = ew_api.body_bytes(delivery.payload or {})
    headers = ew_api.callback_headers(secret, body)
    delivery.attempts = int(delivery.attempts or 0) + 1
    db.session.commit()
    try:
        if request_fn is not None:
            resp = request_fn("POST", key_row.callback_url,
                              headers=headers, data=body)
        else:
            from app.utilities.ssrf_guard import (assert_safe_url,
                                                  guarded_request)
            assert_safe_url(key_row.callback_url, block_private=True)
            resp = guarded_request("POST", key_row.callback_url,
                                   block_private=True, headers=headers,
                                   data=body, timeout=10,
                                   allow_redirects=False)
        status = getattr(resp, "status_code", 0)
        if not (200 <= int(status) < 300):
            raise RuntimeError(f"callback answered {status}")
    except Exception as exc:
        delivery.last_error = str(exc)[:300]
        delivery.next_attempt_at = int(time.time()) + 60 * (2 ** min(delivery.attempts, 4))
        db.session.commit()
        raise
    delivery.status = "delivered"
    delivery.delivered_at = int(time.time())
    delivery.last_error = None
    db.session.commit()
    return {"delivered": True, "attempts": delivery.attempts}

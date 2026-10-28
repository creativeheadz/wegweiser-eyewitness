"""Retention sweep for customer captures (brief 7.8, Phase 3).

Captures are kept for EYEWITNESS_RETENTION_DAYS (default 90) past
the ticket close. Where the close is unknown the request expiry
stands in for it, so a request nobody closed still has its files
deleted. The date rule is a pure function and is tested without a
database; the sweep deletes the files and marks the rows purged.
"""
from __future__ import annotations

import os
import time

DEFAULT_DAYS = 90


def retention_days() -> int:
    try:
        return max(int(os.environ.get("EYEWITNESS_RETENTION_DAYS",
                                      str(DEFAULT_DAYS))), 1)
    except ValueError:
        return DEFAULT_DAYS


def purge_due(*, close_ts, expires_ts, now: int,
              days: int | None = None) -> bool:
    """True when the retention period has run out.

    `close_ts` is the ticket close time when it is known and the
    request expiry otherwise. No date at all is never due.
    """
    base = close_ts if close_ts else expires_ts
    if not base:
        return False
    span = (retention_days() if days is None else int(days)) * 86400
    return int(now) >= int(base) + span


def sweep_purge(now: int | None = None) -> dict:
    """Delete due capture files and mark their rows purged."""
    from app.models import db
    from app.eyewitness.models import Capture, EvidenceRequest
    from app.models.helpdesk import HelpdeskTicket
    moment = int(now if now is not None else time.time())
    days = retention_days()
    purged_files = 0
    purged_rows = 0
    requests = EvidenceRequest.query.all()
    for req in requests:
        ticket = db.session.get(HelpdeskTicket,
                                req.helpdesk_ticket_id)
        close_ts = None
        if ticket is not None and ticket.is_closed:
            close_ts = ticket.updated_ext or None
        if not purge_due(close_ts=close_ts,
                         expires_ts=req.expires_at, now=moment,
                         days=days):
            continue
        caps = Capture.query.filter_by(request_id=req.id).all()
        for cap in caps:
            if cap.purged_at:
                continue
            if cap.file_path and os.path.exists(cap.file_path):
                try:
                    os.remove(cap.file_path)
                    purged_files += 1
                except OSError:
                    continue
            cap.file_path = None
            cap.purged_at = moment
            purged_rows += 1
        if not req.purged_at:
            req.purged_at = moment
    db.session.commit()
    return {"purged_files": purged_files, "purged_rows": purged_rows,
            "days": days}

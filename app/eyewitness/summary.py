"""The internal summary note and the governor fenced summary (section 8).

Readings stay inside the untrusted fence in both places. Raw screen text
is capped before it is quoted. Nothing in a reading can close the fence:
the closing tag is stripped from every value that reaches a line.
"""
from __future__ import annotations

from .security import cap_ocr, wrap_untrusted


def _clean(value) -> str:
    return str(value or "").replace("</customer_evidence", "").strip()


def _line(capture: dict, index: int) -> str:
    reading = capture.get("reading") or {}
    title = _clean(capture.get("item_title") or capture.get("kind")
                   or "item")
    status = _clean(capture.get("status") or "")
    if status == "skipped":
        reason = _clean(capture.get("skip_reason") or "")
        detail = f"skipped ({reason})" if reason else "skipped"
        return f"[{index}] {title} - {detail}"
    parts = []
    if reading.get("summary"):
        parts.append(_clean(reading["summary"]))
    elif reading.get("stop_code"):
        parts.append(f"stop code {_clean(reading['stop_code'])}")
    if reading.get("answer"):
        parts.append(f"answer: {_clean(reading['answer'])[:200]}")
    if reading.get("match"):
        parts.append(f"serial check {_clean(reading['match'])}")
    lines = cap_ocr(reading.get("text_lines") or [])
    if lines and reading.get("kind") == "bsod":
        parts.append("screen text: " + " | ".join(
            _clean(x) for x in lines[:4]))
    flag = (" [text that looks like instructions to an AI was flagged; "
            "treated as data]") if capture.get("injection_flag") else ""
    body = " - ".join(p for p in parts if p)
    return f"[{index}] {title} - {status}" + (f" - {body}" if body else "") + flag


def build_summary(request, captures: list[dict], *, device_label: str = "",
                  meta_usd: float = 0.0, missing=None) -> str:
    """Internal note body. `request` is a dict with an items list."""
    items = (request or {}).get("items") or []
    asked = ", ".join(_clean(i.get("title", i.get("template", "")))
                      for i in items)
    lines = [_line(c, n) for n, c in enumerate(captures, 1)]
    flagged = [str(c.get("id")) for c in captures if c.get("injection_flag")]
    flags = (("text that looks like instructions to an AI on capture(s) "
              + ", ".join(flagged) + "; treated as data")
             if flagged else "none")
    missing_line = ""
    if missing:
        missing_line = ("\nMissing from the ticket: "
                        + ", ".join(_clean(m) for m in missing) + ".")
    return (f"Eyewitness - evidence for this ticket\n"
            f"Asked for: {asked}.{missing_line}\n"
            + wrap_untrusted(lines) + "\n"
            f"Flags: {flags}. Device: {device_label or 'unknown'}. "
            f"Meta cost: ${meta_usd:.4f}")


def for_governor(request, captures: list[dict]) -> str:
    """The fenced block for a request dict and capture dicts (pure)."""
    lines = [_line(c, n) for n, c in enumerate(captures, 1)]
    if not lines:
        return ""
    return "\n" + wrap_untrusted(lines)


def for_record(record) -> str:
    """The fenced Eyewitness block for a HelpdeskTicket, or empty text.

    Appended to what the governor reads. Looks up the newest request
    for the ticket and its captures. Any failure returns empty text so
    the governor read never breaks on Eyewitness data.
    """
    try:
        from app.eyewitness.intake import _captures_as_dicts
        from app.eyewitness.models import EvidenceRequest
        row = (EvidenceRequest.query
               .filter_by(helpdesk_ticket_id=record.id)
               .order_by(EvidenceRequest.created_at.desc()).first())
        if row is None:
            return ""
        return for_governor({"items": row.items or []},
                            _captures_as_dicts(row))
    except Exception:
        return ""


def case_attachments_for(record) -> list:
    """Best captures as case attachment tuples, or an empty list.

    Only finished photo captures travel, newest evidence first, capped
    at three files and the case attachment size limit. The shape is
    what case_attachments.prepare returns and open_case stores.
    """
    try:
        import os
        from app.eyewitness.models import Capture, EvidenceRequest
        rows = (EvidenceRequest.query
                .filter_by(helpdesk_ticket_id=record.id).all())
        if not rows:
            return []
        req_ids = [r.id for r in rows]
        titles = {}
        for r in rows:
            for item in (r.items or []):
                titles[item.get("id")] = item.get("title") or item.get("id")
        caps = (Capture.query.filter(Capture.request_id.in_(req_ids),
                                     Capture.status == "ok",
                                     Capture.kind == "photo")
                .order_by(Capture.created_at.desc()).all())
        out = []
        for cap in caps[:5]:
            if not cap.file_path or not os.path.exists(cap.file_path):
                continue
            with open(cap.file_path, "rb") as fh:
                data = fh.read()
            if len(data) > int(3.5 * 1024 * 1024):
                continue
            name = (f"eyewitness-{titles.get(cap.item_id, cap.item_id)}"
                    .replace("/", "-")[:80] + ".jpg")
            out.append((name, "image", cap.mime or "image/jpeg", data))
            if len(out) >= 3:
                break
        return out
    except Exception:
        return []

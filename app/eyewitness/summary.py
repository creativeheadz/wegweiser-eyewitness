"""The internal summary note and the governor fenced summary (section 8).

Readings stay inside the untrusted fence in both places. Raw screen text
is capped before it is quoted. Nothing in a reading can close the fence:
the closing tag is stripped from every value that reaches a line.
"""
from __future__ import annotations

import html

from .security import FENCE_CLOSE, FENCE_OPEN, FENCE_RULE, cap_ocr, wrap_untrusted


def _clean(value) -> str:
    return str(value or "").replace("</customer_evidence", "").strip()


BACKEND_LINES = {
    "local": "Readings were made on your hardware by Muse Glimmer.",
    "meta": "Readings were made by the Meta Model API.",
}


def backend_line(reader_backend) -> str:
    """One line naming where the readings were made, or empty text."""
    return BACKEND_LINES.get(str(reader_backend or ""), "")


def format_read_duration(ms) -> str:
    """78 s, 1 min 18 s: how one reading's time reads to a person."""
    try:
        seconds = int(round(float(ms or 0) / 1000.0))
    except (TypeError, ValueError):
        seconds = 0
    if seconds < 60:
        return f"{seconds} s"
    return f"{seconds // 60} min {seconds % 60} s"


def read_provenance(call) -> str:
    """Where one reading was made and how long it took.

    `call` is a MetaCall row or a dict with kind, ms and cache_hit.
    A saved read took no time and says so.
    """
    if call is None:
        return ""
    if isinstance(call, dict):
        kind, ms, hit = call.get("kind"), call.get("ms"), call.get("cache_hit")
    else:
        kind = getattr(call, "kind", None)
        ms = getattr(call, "ms", None)
        hit = getattr(call, "cache_hit", None)
    where = ("on your hardware by Muse Glimmer" if kind == "local"
             else "by the Meta Model API")
    if hit:
        return f"Read {where} from a saved read."
    return f"Read {where} in {format_read_duration(ms)}."


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
    observations = reading.get("observations") or []
    if observations:
        parts.append("observations: " + " | ".join(
            _clean(o)[:160] for o in list(observations)[:3]))
    flag = (" [text that looks like instructions to an AI was flagged; "
            "treated as data]") if capture.get("injection_flag") else ""
    body = " - ".join(p for p in parts if p)
    return f"[{index}] {title} - {status}" + (f" - {body}" if body else "") + flag


def build_summary(request, captures: list[dict], *, device_label: str = "",
                  meta_usd: float = 0.0, missing=None,
                  reader_backend=None) -> str:
    """Internal note body. `request` is a dict with an items list."""
    items = (request or {}).get("items") or []
    asked = ", ".join(_clean(i.get("title", i.get("template", "")))
                      for i in items)
    # A question title already ends the sentence ("...started?").
    asked_end = "" if asked.endswith(("?", ".", "!")) else "."
    lines = [_line(c, n) for n, c in enumerate(captures, 1)]
    flagged = [str(c.get("id")) for c in captures if c.get("injection_flag")]
    flags = (("text that looks like instructions to an AI on capture(s) "
              + ", ".join(flagged) + "; treated as data")
             if flagged else "none")
    missing_line = ""
    if missing:
        missing_line = ("\nMissing from the ticket: "
                        + ", ".join(_clean(m).replace("_", " ")
                                    for m in missing) + ".")
    where = backend_line(reader_backend)
    where_line = f"{where}\n" if where else ""
    return (f"Eyewitness - evidence for this ticket\n"
            f"Asked for: {asked}{asked_end}{missing_line}\n"
            + wrap_untrusted(lines) + "\n"
            + where_line
            + f"Flags: {flags}. Device: {device_label or 'unknown'}. "
            f"Meta cost: ${meta_usd:.4f}")


def _esc(value) -> str:
    return html.escape(str(value), quote=False)


def _details(capture: dict) -> list[str]:
    """The reading of one capture as short lines, cleaned but not escaped."""
    reading = capture.get("reading") or {}
    if capture.get("status") == "skipped":
        reason = _clean(capture.get("skip_reason") or "")
        return [f"reason: {reason}"] if reason else []
    out = []
    if reading.get("summary"):
        out.append(_clean(reading["summary"]))
    elif reading.get("stop_code"):
        out.append(f"stop code {_clean(reading['stop_code'])}")
    if reading.get("answer"):
        out.append(f"answer: {_clean(reading['answer'])[:200]}")
    if reading.get("match"):
        out.append(f"serial check {_clean(reading['match'])}")
    lines = cap_ocr(reading.get("text_lines") or [])
    if lines and reading.get("kind") == "bsod":
        out.append("screen text: " + " | ".join(_clean(x) for x in lines[:4]))
    observations = reading.get("observations") or []
    if observations:
        out.append("observations: " + " | ".join(
            _clean(o)[:160] for o in list(observations)[:3]))
    return out


def _item_html(capture: dict) -> str:
    title = _clean(capture.get("item_title") or capture.get("kind") or "item")
    status = _clean(capture.get("status") or "")
    head = f"<strong>{_esc(title)}</strong>" + (f" · {_esc(status)}" if status else "")
    rows = [head] + [_esc(d) for d in _details(capture)]
    if capture.get("injection_flag"):
        rows.append("<strong>Flagged:</strong> text that looks like "
                    "instructions to an AI. Treated as data.")
    return "<li>" + "<br>".join(rows) + "</li>"


def build_summary_html(request, captures: list[dict], *,
                       device_label: str = "", meta_usd: float = 0.0,
                       missing=None, lead: str = "",
                       reader_backend=None) -> str:
    """The internal note set out for a technician: the same facts as
    build_summary, in short sections. Every value is escaped, and the
    untrusted fence stays visible around the readings."""
    items = (request or {}).get("items") or []
    asked = [_clean(i.get("title", i.get("template", ""))) for i in items]
    flagged = [str(c.get("id")) for c in captures if c.get("injection_flag")]
    flags = (("text that looks like instructions to an AI on capture(s) "
              + ", ".join(flagged) + "; treated as data")
             if flagged else "none")
    out = []
    if lead:
        out.append(f"<p><em>{_esc(lead)}</em></p>")
    out.append("<p><strong>Eyewitness - evidence for this ticket</strong></p>")
    if asked:
        out.append("<p><strong>Asked for</strong><br>"
                   + "<br>".join(_esc(a) for a in asked) + "</p>")
    if missing:
        out.append("<p><strong>Missing from the ticket</strong><br>"
                   + _esc(", ".join(_clean(m).replace("_", " ")
                                    for m in missing)) + "</p>")
    out.append(f"<p><code>{_esc(FENCE_OPEN)}</code><br>"
               f"<em>{_esc(FENCE_RULE)}</em></p>")
    if captures:
        out.append("<ol>" + "".join(_item_html(c) for c in captures)
                   + "</ol>")
    out.append(f"<p><code>{_esc(FENCE_CLOSE)}</code></p>")
    where = backend_line(reader_backend)
    if where:
        out.append(f"<p>{_esc(where)}</p>")
    out.append(f"<p><strong>Flags:</strong> {_esc(flags)}<br>"
               f"<strong>Device:</strong> {_esc(device_label or 'unknown')}<br>"
               f"<strong>Meta cost:</strong> ${meta_usd:.4f}</p>")
    return "\n".join(out)


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

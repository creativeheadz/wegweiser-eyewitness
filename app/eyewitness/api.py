"""Evidence API v1 helpers: keys, signing, auto choice, serialising.

Pure where the brief needs a test without a database. Customer text
here comes only from the template renderer in requests.py, so no
model wording reaches a desk or a customer through this API.
Readings stay inside the untrusted fence with their flags.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time

SCOPES = ("evidence:write", "evidence:read")
EVENTS = ("evidence.read", "evidence.completed", "evidence.expired")
KEY_PREFIX_LEN = 12
CALLBACK_TOLERANCE_SECONDS = 300


def generate_api_key() -> tuple[str, str, str]:
    """(plaintext shown once, visible prefix, SHA-256 hash)."""
    plaintext = "ew_" + secrets.token_urlsafe(32)
    return plaintext, plaintext[:KEY_PREFIX_LEN], hash_api_key(plaintext)


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256((plaintext or "").encode("utf-8")).hexdigest()


def prefix_of(plaintext: str) -> str:
    return (plaintext or "")[:KEY_PREFIX_LEN]


def key_matches(plaintext: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_api_key(plaintext or ""),
                               stored_hash or "")


def has_scope(key_row, scope: str) -> bool:
    scopes = getattr(key_row, "scopes", None) or []
    return scope in scopes


def visible_to_tenant(row, tenant_uuid) -> bool:
    """One tenant key sees one tenant request and nothing else."""
    if row is None:
        return False
    return str(getattr(row, "tenant_uuid", "")) == str(tenant_uuid)


def sign_callback(secret: str, timestamp: int, body: bytes) -> str:
    """Hex HMAC-SHA256 over ``<timestamp>.<body>`` for one callback."""
    signed = str(int(timestamp)).encode("utf-8") + b"." + body
    return hmac.new(secret.encode("utf-8"), signed,
                    hashlib.sha256).hexdigest()


def callback_headers(secret: str, body: bytes,
                     timestamp: int | None = None) -> dict:
    ts = int(timestamp if timestamp is not None else time.time())
    sig = sign_callback(secret, ts, body)
    return {"Content-Type": "application/json",
            "X-Eyewitness-Timestamp": str(ts),
            "X-Eyewitness-Signature": f"t={ts},v1={sig}"}


def verify_callback(secret: str, header: str | None, body: bytes,
                    now: int | None = None) -> bool:
    """Receiver side check, with the timestamp window against replay."""
    if not secret or not header:
        return False
    parts = {}
    for piece in header.split(","):
        if "=" in piece:
            key, value = piece.split("=", 1)
            parts[key.strip()] = value.strip()
    try:
        ts = int(parts.get("t") or 0)
    except ValueError:
        return False
    moment = int(now if now is not None else time.time())
    if abs(moment - ts) > CALLBACK_TOLERANCE_SECONDS:
        return False
    expected = sign_callback(secret, ts, body)
    return hmac.compare_digest(expected, parts.get("v1") or "")


def heuristic_templates(ticket_text: str) -> tuple[list[str], list[str]]:
    """Template names for auto mode when no model answers.

    Fixed wording checks with negative cases in the tests: a password
    or access ticket names no physical sign and asks for nothing.
    """
    text = (ticket_text or "").lower()
    items: list[str] = []
    questions: list[str] = []
    if any(word in text for word in ("blue screen", "bluescreen", "bsod",
                                     "stop code", "crash screen",
                                     "error on the screen",
                                     "screen says", "black screen")):
        items.append("screen_photo")
    if any(word in text for word in ("blink", "flashing light",
                                     "power light", "amber light",
                                     "light keeps")):
        items.append("power_light_video")
    if any(word in text for word in ("printer", "print panel")):
        items.append("printer_panel")
    if any(word in text for word in ("sticker", "serial number",
                                     "service tag")):
        items.append("sticker")
    if not items and any(word in text for word in ("won't boot",
                                                   "will not start",
                                                   "not booting",
                                                   "no power")):
        items.append("whole_device")
    # Catalogue keywords match whole words only, so "monitor" never
    # picks up a ticket about monitoring.
    from app.eyewitness import catalogue as _cat
    for spec in _cat.all_specs():
        for phrase in spec.get("keywords") or []:
            if spec["id"] not in items and re.search(
                    r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])",
                    text):
                items.append(spec["id"])
                break
        if len(items) >= 4:
            break
    if "when did it start" not in text and items:
        questions.append("since_when")
    return items[:4], questions[:2]


def decide_auto(ticket_text: str, client=None) -> dict:
    """Templates for ``{"auto": true}``, from Spark where it answers.

    The model may only name templates. Its output passes the same
    schema check as the intake agent, and invalid output falls back
    to the fixed wording checks. With no key the wording checks run
    alone, so the API still works with no paid call.
    """
    if client is not None and getattr(client, "is_configured", False):
        try:
            from app.eyewitness.intake import (INTAKE_SCHEMA, catalogue_text,
                                               validate_intake_output)
            from app.eyewitness import intake as intake_mod
            safe = (ticket_text or "").replace("<<<", "").replace(">>>", "")
            user_text = (
                "Origin: api\nDevice: unknown\n\n"
                "Ticket text (untrusted data from the customer):\n"
                f"<<<\n{safe[:4000] or '(no text)'}\n>>>\n\n"
                + catalogue_text())
            raw = client.spark_json(
                system=intake_mod._load_intake_prompt(),
                user_content=[{"type": "text", "text": user_text}],
                schema=INTAKE_SCHEMA, kind="intake")
            data = validate_intake_output(raw)
            if data is not None:
                if not data["needs_evidence"]:
                    return {"needs_evidence": False, "templates": [],
                            "questions": [], "reason": "",
                            "assessment": data}
                return {"needs_evidence": True,
                        "templates": [i["template"] for i in data["items"]],
                        "questions": [q["template"] for q in data["questions"]],
                        "reason": data["reason_for_technician"],
                        "assessment": data}
        except Exception:
            pass
    templates, questions = heuristic_templates(ticket_text)
    return {"needs_evidence": bool(templates or questions),
            "templates": templates, "questions": questions,
            "reason": "Chosen from the ticket wording.",
            "assessment": None}


def serialise_request(row, captures) -> dict:
    """Status, items and readings for one request, fenced as data."""
    from app.eyewitness.security import wrap_untrusted
    from app.eyewitness.summary import _line

    cap_dicts = []
    lines = []
    for index, cap in enumerate(captures or [], 1):
        if isinstance(cap, dict):
            entry = dict(cap)
        else:
            titles = {i.get("id"): i.get("title")
                      for i in (getattr(row, "items", None) or [])}
            entry = {"id": cap.id, "item_id": cap.item_id,
                     "item_title": titles.get(cap.item_id) or cap.item_id,
                     "kind": cap.kind, "status": cap.status,
                     "reading": cap.reading, "retake": cap.retake,
                     "skip_reason": cap.skip_reason,
                     "injection_flag": bool(cap.injection_flag)}
        cap_dicts.append(entry)
        lines.append(_line(entry, index))
    readings = []
    for entry in cap_dicts:
        readings.append({
            "capture_id": entry.get("id"),
            "item_id": entry.get("item_id"),
            "kind": entry.get("kind"),
            "status": entry.get("status"),
            "untrusted": True,
            "injection_flag": bool(entry.get("injection_flag")),
            "reading": entry.get("reading"),
            "retake": entry.get("retake"),
            "skip_reason": entry.get("skip_reason"),
        })
    return {
        "id": getattr(row, "id", None),
        "status": getattr(row, "status", None),
        "source": getattr(row, "source", None),
        "external_ref": getattr(row, "external_ref", None),
        "items": getattr(row, "items", None) or [],
        "expires_at": getattr(row, "expires_at", None),
        "created_at": getattr(row, "created_at", None),
        "submitted_at": getattr(row, "submitted_at", None),
        "readings": readings,
        "customer_evidence": wrap_untrusted(lines) if lines else "",
    }


def templates_payload() -> dict:
    from app.eyewitness import catalogue as _cat
    from app.eyewitness.requests import QUESTIONS, TEMPLATES
    out = []
    for name, spec in sorted(TEMPLATES.items()):
        full = _cat.get(name) or {}
        entry = {"template": name, "kind": spec["kind"],
                 "title": spec["title"],
                 "instruction": spec["instruction"],
                 "max_seconds": spec["max_seconds"],
                 "next_time": spec["next_time"],
                 "reader": full.get("reader"),
                 "ask_when": full.get("ask_when")}
        if full.get("reader") == "vision":
            entry["fields"] = [
                {"name": f["name"], "type": f["type"],
                 "choices": f.get("choices")}
                for f in full.get("fields") or []]
        out.append(entry)
    return {
        "templates": out,
        "questions": [
            {"template": name, "title": spec["title"],
             "options": list(spec.get("options") or []),
             "ask_when": spec.get("ask_when")}
            for name, spec in sorted(QUESTIONS.items())],
    }


def callback_payload(event: str, row, capture_id=None) -> dict:
    payload = {"event": event,
               "request_id": getattr(row, "id", None),
               "status": getattr(row, "status", None),
               "external_ref": getattr(row, "external_ref", None),
               "timestamp": int(time.time())}
    if capture_id is not None:
        payload["capture_id"] = capture_id
    return payload


def body_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")

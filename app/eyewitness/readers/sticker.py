"""Sticker reader (brief 7.3): barcode first, then one Spark read.

Serial normalisation and matching are pure and tested. Placeholder
serials never match. A confirmed mismatch returns a retake, because a
photo of a different machine would send the work to the wrong device.
As with screens, Spark picks a retake reason from a fixed list and the
customer reads only the fixed sentence for it (brief 7.8).
"""
from __future__ import annotations

import os
import re

from .quality import assess_image

PROMPT_VERSION = "sticker_read_v2"

RETAKE_ADVICE = {
    "glare": "There is glare on the label. Tilt the phone slightly and "
             "try again.",
    "blur": "The label is out of focus. Tap the label on your phone "
            "screen to focus, then try again.",
    "too_far": "The label is too small in the photo. Move closer so it "
               "fills most of the photo.",
    "cropped": "Part of the label is cut off. Step back a little so the "
               "whole label fits in the photo.",
    "other": "The label cannot be read. Move closer and keep the whole "
             "label in the photo.",
}

_PLACEHOLDERS = {"", "none", "unknown", "n/a", "na", "tbd",
                 "serialnumber", "systemserialnumber", "0123456789",
                 "xxxxxxxx", "defaultstring", "tobe filled by o.e.m.",
                 "to be filled by o.e.m."}

STICKER_SCHEMA = {
    "type": "object",
    "properties": {
        "manufacturer": {"type": ["string", "null"]},
        "model": {"type": ["string", "null"]},
        "serial_text": {"type": ["string", "null"]},
        "service_tag": {"type": ["string", "null"]},
        "text_lines": {"type": "array", "items": {"type": "string"}},
        "language": {"type": ["string", "null"]},
        "legible": {"type": "boolean"},
        "retake_reason": {"type": "string",
                          "enum": ["none", *RETAKE_ADVICE]},
        "instruction_like_text": {"type": "boolean"},
        "instruction_excerpt": {"type": ["string", "null"]},
    },
    "required": ["manufacturer", "model", "serial_text", "service_tag",
                 "text_lines", "language", "legible", "retake_reason",
                 "instruction_like_text", "instruction_excerpt"],
    "additionalProperties": False,
}


def real_serial(value) -> str | None:
    """A serial worth comparing, or None for placeholders and empties."""
    s = re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()
    placeholders = {re.sub(r"[^a-z0-9]", "", p) for p in _PLACEHOLDERS}
    if not s or s.lower() in placeholders or len(s) < 4:
        return None
    if len(set(s)) == 1:  # XXXX or 0000 (house rule in rmm_links)
        return None
    return s


def serial_match(read_value, known_values) -> str:
    """match, mismatch or unknown against the ticket device serials."""
    got = real_serial(read_value)
    if got is None:
        return "unknown"
    known = {real_serial(v) for v in (known_values or [])}
    known.discard(None)
    if not known:
        return "unknown"
    return "match" if got in known else "mismatch"


def _load_prompt() -> str:
    path = os.path.join(os.path.dirname(__file__), "..", "prompts",
                        "sticker_read_v2.md")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _dell_link(manufacturer, serial) -> str | None:
    if not serial or "dell" not in str(manufacturer or "").lower():
        return None
    return ("https://www.dell.com/support/home/en-uk/product-support/"
            f"servicetag/{serial}")


def _verdict_from_reading(raw: dict, codes: list[str], quality: dict,
                          known_serials) -> dict:
    """Pure verdict step, tested without a model call."""
    from app.eyewitness.security import cap_ocr
    candidates = list(codes or []) + [raw.get("service_tag"),
                                      raw.get("serial_text")]
    match = "unknown"
    serial = None
    for cand in candidates:
        if real_serial(cand) is None:
            continue
        serial = real_serial(cand)
        m = serial_match(cand, known_serials)
        if m == "match":
            match, serial = "match", real_serial(cand)
            break
        if m == "mismatch" and match == "unknown":
            match = "mismatch"
    reading = {
        "manufacturer": raw.get("manufacturer") or None,
        "model": raw.get("model") or None,
        "serial": serial,
        "serial_text": raw.get("serial_text") or None,
        "service_tag": raw.get("service_tag") or None,
        "text_lines": cap_ocr(raw.get("text_lines") or []),
        "language": raw.get("language") or None,
        "legible": bool(raw.get("legible")),
        "codes": list(codes or []),
        "match": match,
        "quality": quality,
        "prompt_version": PROMPT_VERSION,
        "instruction_excerpt": (raw.get("instruction_excerpt") or "")[:200]
        or None,
    }
    link = _dell_link(reading["manufacturer"], serial)
    if link:
        reading["support_link"] = link
    injection = bool(raw.get("instruction_like_text"))
    if not reading["legible"]:
        reason = raw.get("retake_reason")
        if reason not in RETAKE_ADVICE:
            reason = "other"
        reading["retake_reason"] = reason
        return {"verdict": "retake",
                "retake": {"reason": "illegible",
                           "advice": RETAKE_ADVICE[reason]},
                "reading": reading, "injection_flag": injection}
    if match == "mismatch":
        return {"verdict": "retake",
                "retake": {"reason": "different_machine",
                           "advice": "This looks like a different "
                                     "machine. Photograph the label on "
                                     "the machine named in your "
                                     "ticket."},
                "reading": reading, "injection_flag": injection}
    parts = ["label read"]
    if reading["manufacturer"]:
        parts.append(str(reading["manufacturer"]))
    if reading["model"]:
        parts.append(str(reading["model"]))
    if serial:
        parts.append(f"serial {serial}")
    parts.append(f"serial check: {match}")
    reading["summary"] = ", ".join(parts)
    return {"verdict": "ok", "retake": None, "reading": reading,
            "injection_flag": injection}


def read_sticker(data: bytes, *, known_serials=None, client=None,
                 capture_id=None, intake_run_id=None) -> dict:
    """{verdict, retake, reading, injection_flag} (reader contract)."""
    check = assess_image(data)
    if check["verdict"] == "retake":
        return {"verdict": "retake", "retake": check["retake"],
                "reading": {"quality": check["quality"],
                            "prompt_version": PROMPT_VERSION},
                "injection_flag": False}
    from .screen import _image_part, decode_codes
    codes = decode_codes(data)
    if client is None:
        from app.eyewitness.meta.client import db_bound_client
        try:
            client = db_bound_client()
        except Exception:
            client = None
        if client is not None and not client.is_configured:
            return {"verdict": "ok", "retake": None,
                    "reading": {"stub": True,
                                "prompt_version": PROMPT_VERSION,
                                "codes": codes,
                                "quality": check["quality"],
                                "match": "unknown",
                                "summary": "photo received"},
                    "injection_flag": False}
    try:
        raw = client.spark_json(
            system=_load_prompt(),
            user_content=[
                {"type": "text", "text": "Read this device label."},
                _image_part(data)],
            schema=STICKER_SCHEMA, kind="spark",
            capture_id=capture_id, intake_run_id=intake_run_id)
    except Exception:
        return {"verdict": "ok", "retake": None,
                "reading": {"stub": True, "prompt_version": PROMPT_VERSION,
                            "codes": codes, "quality": check["quality"],
                            "match": "unknown",
                            "summary": "photo received"},
                "injection_flag": False}
    return _verdict_from_reading(raw, codes, check["quality"],
                                 known_serials)

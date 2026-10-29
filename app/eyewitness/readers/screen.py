"""Screen photo reader (brief 7.3). Prompt version screen_read_v2.

The current Windows 11 crash screen is black, with no sad face and no
QR code. The stop code sits in small text at the bottom. The v2 prompt
teaches that layout first and treats a QR code as optional. Older blue
screens still parse under the same fields.

Flow: quality gate (no model call on failure), zxing QR and barcode
decode, then one Spark structured read. Text in the image is data.

No model text reaches the customer (brief 7.8). When a photo cannot be
read, Spark picks a reason from a fixed list and the customer sees the
fixed sentence for that reason.
"""
from __future__ import annotations

import base64
import io
import os

from .quality import assess_image

PROMPT_VERSION = "screen_read_v2"

# The advice for the one failure the new layout invites: the stop code
# is small text at the bottom and a photo taken too far away loses it.
BOTTOM_LINE_ADVICE = ("Move closer so the small line at the bottom of "
                      "the screen fills more of the photo.")

RETAKE_ADVICE = {
    "bottom_line": BOTTOM_LINE_ADVICE,
    "glare": "There is glare on the screen. Tilt the phone slightly and "
             "try again.",
    "moire": "Stripes are hiding the text. Step back a little and tap "
             "your phone screen to focus.",
    "blur": "The photo is out of focus. Hold the phone 30 cm away and "
            "tap your phone screen to focus.",
    "cropped": "Part of the screen is cut off. Step back so the whole "
               "screen fits in the photo.",
    "other": "The text cannot be read. Hold the phone 30 cm away and tap "
             "your phone screen to focus.",
}
NO_SCREEN_ADVICE = ("No screen could be read in the photo. Fill the "
                    "photo with the screen and try again.")

SCREEN_SCHEMA = {
    "type": "object",
    "properties": {
        "screen_found": {"type": "boolean"},
        "kind": {"type": "string",
                 "enum": ["bsod", "post", "boot_error", "app_error",
                          "login", "desktop", "other"]},
        "text_lines": {"type": "array", "items": {"type": "string"}},
        "stop_code": {"type": ["string", "null"]},
        "failing_module": {"type": ["string", "null"]},
        "error_codes": {"type": "array", "items": {"type": "string"}},
        "product_names": {"type": "array", "items": {"type": "string"}},
        "language": {"type": ["string", "null"]},
        "legible": {"type": "boolean"},
        "retake_reason": {"type": "string",
                          "enum": ["none", *RETAKE_ADVICE]},
        "instruction_like_text": {"type": "boolean"},
        "instruction_excerpt": {"type": ["string", "null"]},
    },
    "required": ["screen_found", "kind", "text_lines", "stop_code",
                 "failing_module", "error_codes", "product_names",
                 "language", "legible", "retake_reason",
                 "instruction_like_text", "instruction_excerpt"],
    "additionalProperties": False,
}


def _load_prompt() -> str:
    path = os.path.join(os.path.dirname(__file__), "..", "prompts",
                        "screen_read_v2.md")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def decode_codes(data: bytes) -> list[str]:
    """QR and barcode payloads via zxing-cpp when installed, else []."""
    try:
        import zxingcpp
        from PIL import Image
        with Image.open(io.BytesIO(data)) as img:
            results = zxingcpp.read_barcodes(img.convert("RGB"))
        return [r.text for r in results if getattr(r, "text", None)]
    except Exception:
        return []


def _image_part(data: bytes) -> dict:
    """An OpenAI image part. Large photos are shrunk to a 1600 px long
    edge and re-encoded as JPEG, which also drops EXIF and GPS data."""
    mime = "image/png"
    payload = data
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as img:
            fmt = (img.format or "").lower()
            mime = {"jpeg": "image/jpeg", "png": "image/png",
                    "webp": "image/webp"}.get(fmt, "image/png")
            if max(img.size) > 1600:
                img = img.convert("RGB")
                img.thumbnail((1600, 1600))
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=88)
                payload = buf.getvalue()
                mime = "image/jpeg"
    except Exception:
        mime = "image/jpeg"
    url = f"data:{mime};base64," + base64.b64encode(payload).decode()
    return {"type": "image_url", "image_url": {"url": url}}


def _verdict_from_reading(raw: dict, codes: list[str],
                          quality: dict) -> dict:
    """Pure verdict step, tested without a model call."""
    from app.eyewitness.security import cap_ocr
    text_lines = cap_ocr(raw.get("text_lines") or [])
    stop_code = raw.get("stop_code") or None
    kind = raw.get("kind") or "other"
    legible = bool(raw.get("legible"))
    reading = {
        "screen_found": bool(raw.get("screen_found")),
        "kind": kind,
        "text_lines": text_lines,
        "stop_code": stop_code,
        "failing_module": raw.get("failing_module") or None,
        "error_codes": list(raw.get("error_codes") or [])[:10],
        "product_names": list(raw.get("product_names") or [])[:10],
        "language": raw.get("language") or None,
        "legible": legible,
        "codes": codes,
        "qr_present": bool(codes),
        "quality": quality,
        "prompt_version": PROMPT_VERSION,
        "instruction_excerpt": (raw.get("instruction_excerpt") or "")[:200]
        or None,
    }
    injection = bool(raw.get("instruction_like_text"))
    if not reading["screen_found"]:
        return {"verdict": "retake",
                "retake": {"reason": "no_screen",
                           "advice": NO_SCREEN_ADVICE},
                "reading": reading, "injection_flag": injection}
    if not legible:
        reason = raw.get("retake_reason")
        if reason not in RETAKE_ADVICE:
            reason = "other"
        if kind == "bsod" and not stop_code:
            reason = "bottom_line"
        reading["retake_reason"] = reason
        return {"verdict": "retake",
                "retake": {"reason": "illegible",
                           "advice": RETAKE_ADVICE[reason]},
                "reading": reading, "injection_flag": injection}
    label = {"bsod": "stop screen", "post": "startup screen",
             "boot_error": "boot error", "app_error": "app error",
             "login": "login screen", "desktop": "desktop"}.get(
                 kind, "screen")
    summary = label
    if stop_code:
        summary += f", stop code {stop_code}"
    if codes:
        summary += f"; code in photo: {codes[0][:80]}"
    reading["summary"] = summary
    return {"verdict": "ok", "retake": None, "reading": reading,
            "injection_flag": injection}


def read_screen(data: bytes, *, client=None, capture_id=None,
                intake_run_id=None, tenant_uuid=None) -> dict:
    """{verdict, retake, reading, injection_flag} (reader contract)."""
    check = assess_image(data)
    # The black crash screen passes the gate on its bright text. A frame
    # dark all over, a small file, glare or blur stop here with no call.
    if check["verdict"] == "retake":
        return {"verdict": "retake", "retake": check["retake"],
                "reading": {"quality": check["quality"],
                            "prompt_version": PROMPT_VERSION},
                "injection_flag": False}
    codes = decode_codes(data)
    if client is None:
        from app.eyewitness.meta.client import MetaNotConfigured, reader_client
        try:
            client = reader_client(tenant_uuid)
        except Exception:
            client = None
        if client is not None and not client.is_configured:
            return {"verdict": "ok", "retake": None,
                    "reading": {"stub": True,
                                "prompt_version": PROMPT_VERSION,
                                "codes": codes,
                                "quality": check["quality"],
                                "summary": "photo received"},
                    "injection_flag": False}
    try:
        raw = client.spark_json(
            system=_load_prompt(),
            user_content=[
                {"type": "text", "text": "Read this screen photo."},
                _image_part(data)],
            schema=SCREEN_SCHEMA, kind="spark",
            capture_id=capture_id, intake_run_id=intake_run_id)
    except Exception:
        # No key, cap reached or API down: the photo stays with the
        # ticket and the technician reads it. Only quality rejects.
        return {"verdict": "ok", "retake": None,
                "reading": {"stub": True, "prompt_version": PROMPT_VERSION,
                            "codes": codes, "quality": check["quality"],
                            "summary": "photo received"},
                "injection_flag": False}
    return _verdict_from_reading(raw, codes, check["quality"])

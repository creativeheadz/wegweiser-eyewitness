"""Generic vision reader for catalogue templates. Prompt vision_read_v1.

A model describes the photo and fills the template fields. Code
decides the verdict and builds the summary. No model text reaches a
customer: retake advice comes from the template file.
"""
from __future__ import annotations

import os

from .quality import assess_image
from .screen import _image_part

PROMPT_VERSION = "vision_read_v1"
OTHER_ADVICE = ("The photo cannot be read. Take a new one in good "
                "light and try again.")


def _load_base_prompt() -> str:
    path = os.path.join(os.path.dirname(__file__), "..", "prompts",
                        "vision_read_v1.md")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def build_system(spec: dict) -> str:
    """The fixed rules plus this item, its look_for and its fields."""
    lines = [_load_base_prompt().rstrip(), "",
             f"## Item: {spec.get('title') or spec.get('id')}",
             f"What to look for: {spec.get('look_for') or ''}",
             "", "Fields to fill:"]
    for f in spec.get("fields") or []:
        line = f"- {f['name']} ({f['type']}): {f['describe']}"
        if f.get("choices"):
            line += " Choices: " + ", ".join(f["choices"]) + "."
        lines.append(line)
    return "\n".join(lines)


def build_schema(spec: dict) -> dict:
    """The strict schema for this template, with every field required."""
    props: dict = {}
    for f in spec.get("fields") or []:
        tp = f["type"]
        if tp == "text":
            props[f["name"]] = {"type": ["string", "null"]}
        elif tp == "choice":
            props[f["name"]] = {"type": "string",
                                "enum": list(f.get("choices") or [])}
        elif tp == "list":
            item: dict = {"type": "string"}
            if f.get("choices"):
                item = {"type": "string", "enum": list(f["choices"])}
            props[f["name"]] = {"type": "array", "items": item}
        else:  # yes_no
            props[f["name"]] = {"type": "boolean"}
    retake_keys = [k for k in (spec.get("retake") or {})
                   if k not in ("not_found", "other")]
    return {
        "type": "object",
        "properties": {
            "found": {"type": "boolean"},
            "legible": {"type": "boolean"},
            "retake_reason": {"type": "string",
                              "enum": ["none", *retake_keys, "other"]},
            "text_lines": {"type": "array", "items": {"type": "string"}},
            "observations": {"type": "array",
                             "items": {"type": "string"}},
            "fields": {"type": "object", "properties": props,
                       "required": [f["name"] for f in spec.get("fields") or []],
                       "additionalProperties": False},
            "instruction_like_text": {"type": "boolean"},
            "instruction_excerpt": {"type": ["string", "null"]},
        },
        "required": ["found", "legible", "retake_reason", "text_lines",
                     "observations", "fields", "instruction_like_text",
                     "instruction_excerpt"],
        "additionalProperties": False,
    }


def _sanitise_fields(raw_fields, spec: dict) -> dict:
    out: dict = {}
    raw_fields = raw_fields if isinstance(raw_fields, dict) else {}
    for f in spec.get("fields") or []:
        name, tp = f["name"], f["type"]
        value = raw_fields.get(name)
        if tp == "text":
            out[name] = (str(value)[:120] if isinstance(value, str)
                         and value.strip() else None)
        elif tp == "choice":
            out[name] = value if value in (f.get("choices") or []) else None
        elif tp == "list":
            vals = value if isinstance(value, list) else []
            if f.get("choices"):
                vals = [v for v in vals if v in f["choices"]]
            out[name] = [str(v)[:120] for v in vals][:12]
        else:  # yes_no
            out[name] = value if isinstance(value, bool) else None
    return out


def _summary(spec: dict, fields: dict) -> str:
    parts = [str(spec.get("label") or "photo")]
    for f in spec.get("fields") or []:
        if not f.get("in_summary"):
            continue
        value = fields.get(f["name"])
        if value is None or value == "" or value == []:
            continue
        if f["type"] == "yes_no":
            if value is True:
                parts.append(f["name"].replace("_", " "))
        elif f["type"] == "list":
            shown = ", ".join(str(v).replace("_", " ") for v in value)
            if shown:
                parts.append(f"{f['name']}: {shown}")
        elif f["type"] == "choice":
            parts.append(f"{f['name']}: {str(value).replace('_', ' ')}")
        else:
            parts.append(str(value))
    text = "; ".join(parts)
    return text[:300]


def verdict_from_reading(raw: dict, spec: dict, quality: dict) -> dict:
    """The pure verdict step, tested without a model call."""
    from app.eyewitness.security import cap_ocr
    fields = _sanitise_fields(raw.get("fields") or {}, spec)
    text_lines = cap_ocr(raw.get("text_lines") or [])
    observations = [str(o)[:160] for o in list(raw.get("observations") or [])[:5]]
    found = bool(raw.get("found"))
    legible = bool(raw.get("legible"))
    reading = {
        "template": spec.get("id"),
        "catalogue_version": spec.get("version"),
        "prompt_version": PROMPT_VERSION,
        "found": found,
        "legible": legible,
        "fields": fields,
        "observations": observations,
        "text_lines": text_lines,
        "quality": quality,
        "instruction_excerpt": (str(raw.get("instruction_excerpt") or "")[:200] or None),
    }
    injection = bool(raw.get("instruction_like_text"))
    retake_map = spec.get("retake") or {}
    if not found:
        reading["summary"] = str(spec.get("label") or "photo")
        return {"verdict": "retake",
                "retake": {"reason": "not_found",
                           "advice": retake_map.get("not_found") or OTHER_ADVICE},
                "reading": reading, "injection_flag": injection}
    if not legible:
        reason = raw.get("retake_reason")
        allowed = [k for k in retake_map if k != "not_found"] + ["other"]
        if reason not in allowed:
            reason = "other"
        reading["retake_reason"] = reason
        return {"verdict": "retake",
                "retake": {"reason": reason,
                           "advice": retake_map.get(reason) or OTHER_ADVICE},
                "reading": reading, "injection_flag": injection}
    reading["summary"] = _summary(spec, fields)
    return {"verdict": "ok", "retake": None, "reading": reading,
            "injection_flag": injection}


def read_vision(data: bytes, spec: dict, *, client=None, capture_id=None,
                intake_run_id=None, tenant_uuid=None) -> dict:
    """Reader contract for one vision template photo."""
    try:
        check = assess_image(data)
    except Exception:
        return {"verdict": "retake",
                "retake": {"reason": "unreadable",
                           "advice": OTHER_ADVICE},
                "reading": {"quality": {}, "prompt_version": PROMPT_VERSION,
                            "template": spec.get("id"),
                            "catalogue_version": spec.get("version")},
                "injection_flag": False}
    if check["verdict"] == "retake":
        return {"verdict": "retake", "retake": check["retake"],
                "reading": {"quality": check["quality"],
                            "prompt_version": PROMPT_VERSION,
                            "template": spec.get("id"),
                            "catalogue_version": spec.get("version")},
                "injection_flag": False}
    if client is None:
        try:
            from app.eyewitness.meta.client import reader_client
            client = reader_client(tenant_uuid)
        except Exception:
            client = None
    stub = {"verdict": "ok", "retake": None,
            "reading": {"stub": True, "prompt_version": PROMPT_VERSION,
                        "template": spec.get("id"),
                        "catalogue_version": spec.get("version"),
                        "quality": check["quality"],
                        "summary": "photo received"},
            "injection_flag": False}
    if client is None or not getattr(client, "is_configured", False):
        return stub
    try:
        raw = client.spark_json(
            system=build_system(spec),
            user_content=[{"type": "text", "text": "Read this photo."},
                          _image_part(data)],
            schema=build_schema(spec), kind="spark",
            capture_id=capture_id, intake_run_id=intake_run_id)
    except Exception:
        return stub
    return verdict_from_reading(raw or {}, spec, check["quality"])

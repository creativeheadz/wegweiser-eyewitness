"""The intake agent and hold/release logic (brief 7.4, 7.6).

Phase 1 ships validation + the hold/release state machine as pure,
tested decisions. Phase 2 wires `on_new_ticket` into the inbound poll
and runs the Spark call from `run_intake`.
"""
from __future__ import annotations

from .requests import QUESTIONS, TEMPLATES

MISSING_KEYS = ("affected_device", "since_when", "how_often",
                "what_changed", "impact", "error_text")
PHYSICAL = ("bsod_or_boot_failure", "blinking_lights", "no_boot",
            "display_error", "printer_panel", "beeps", "other_physical",
            "none")


def validate_intake_output(data) -> dict | None:
    """Validate Spark's structured output. Invalid means no request."""
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("needs_evidence"), bool):
        return None
    missing = data.get("missing") or []
    if not isinstance(missing, list) or any(m not in MISSING_KEYS for m in missing):
        return None
    if data.get("physical_symptom", "none") not in PHYSICAL:
        return None
    items = data.get("items") or []
    if not isinstance(items, list) or len(items) > 4:
        return None
    for item in items:
        if not isinstance(item, dict) or item.get("template") not in TEMPLATES:
            return None
    questions = data.get("questions") or []
    if not isinstance(questions, list) or len(questions) > 2:
        return None
    for q in questions:
        if not isinstance(q, dict) or q.get("template") not in QUESTIONS:
            return None
    reason = data.get("reason_for_technician")
    if reason is not None and not isinstance(reason, str):
        return None
    return {"needs_evidence": data["needs_evidence"],
            "missing": list(missing),
            "physical_symptom": data.get("physical_symptom", "none"),
            "items": [{"template": i["template"]} for i in items],
            "questions": [{"template": q["template"]} for q in questions],
            "reason_for_technician": (reason or "")[:1000]}


def should_hold(origin: str, *, tenant_enabled: bool,
                connector_supported: bool = True) -> bool:
    """ORIGIN_WEGWEISER is never Eyewitness's to judge (M1)."""
    if origin == "wegweiser":
        return False
    if origin not in ("desk", "help"):
        return False
    return bool(tenant_enabled and connector_supported)


def release_due(*, done: bool, all_resolved: bool, expired: bool,
                manual: bool, held_seconds: int,
                max_hold_seconds: int) -> bool:
    """Every release trigger from 7.6 in one place."""
    return bool(done or all_resolved or expired or manual
                or held_seconds >= max_hold_seconds)

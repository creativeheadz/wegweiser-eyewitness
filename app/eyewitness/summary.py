"""The internal summary note + the governor's fenced summary (section 8)."""
from __future__ import annotations

from .security import wrap_untrusted


def _line(capture: dict, index: int) -> str:
    reading = capture.get("reading") or {}
    detail = reading.get("summary") or reading.get("stub") and "received (reader stub)" or ""
    extra = ""
    if reading.get("stop_code"):
        extra = f" — stop code {reading['stop_code']}"
    flag = " [injection-like text flagged]" if capture.get("injection_flag") else ""
    return (f"[{index}] {capture.get('item_title') or capture.get('kind')} — "
            f"{capture.get('status')}{extra}{flag}")


def build_summary(request, captures: list[dict], *, device_label: str = "",
                  meta_usd: float = 0.0) -> str:
    """Internal-note body. Readings stay inside the untrusted fence."""
    asked = ", ".join(i.get("title", i.get("template", "")) for i in (request.get("items") or []))
    lines = [_line(c, n) for n, c in enumerate(captures, 1)]
    flags = ("injection flag on capture(s) "
             + ", ".join(str(c.get("id")) for c in captures if c.get("injection_flag"))
             ) if any(c.get("injection_flag") for c in captures) else "none"
    return (f"Eyewitness — evidence for this ticket\n"
            f"Asked for: {asked}.\n"
            + wrap_untrusted(lines) + "\n"
            f"Flags: {flags}.  Device: {device_label or 'unknown'}.  "
            f"Meta cost: ${meta_usd:.4f}")


def for_governor(request, captures: list[dict]) -> str:
    """The same fenced block appended to what the governor reads (M7)."""
    lines = [_line(c, n) for n, c in enumerate(captures, 1)]
    if not lines:
        return ""
    return "\n" + wrap_untrusted(lines)

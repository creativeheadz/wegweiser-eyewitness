"""Evidence templates and request lifecycle (brief sections 7.5-7.7).

Customer-facing text is rendered here from templates only. No model
text reaches a customer: `custom` items and any second request for the
same ticket wait for a technician (`needs_approval`).
"""
from __future__ import annotations

import html
import os
import re
import secrets
import time

from . import catalogue as _catalogue

# Built from the catalogue files, with exactly the shape consumers
# have always seen. The full spec stays in the catalogue package.
TEMPLATES: dict[str, dict] = {
    spec["id"]: {"kind": spec["kind"], "max_seconds": spec.get("max_seconds"),
                 "next_time": bool(spec.get("next_time", False)),
                 "title": spec["title"], "instruction": spec["instruction"]}
    for spec in _catalogue.all_specs()}

QUESTIONS: dict[str, dict] = {
    "since_when": {"title": "When did it start?",
                   "options": ["today", "this week", "longer", "can't say"],
                   "ask_when": "The ticket does not say when the problem started."},
    "how_often": {"title": "How often does it happen?",
                  "options": ["once", "several times", "constantly"],
                  "ask_when": "The ticket does not say whether the problem happened once or keeps happening."},
    "what_changed": {"title": "Did anything change just before it started?",
                     "options": [], "free_text_max": 200,
                     "ask_when": "The ticket does not say whether anything changed just before the problem started, such as an update, a move or new hardware."},
}

LETTERS = "ABCDEFGH"


def make_item(template: str, *, custom_text: str | None = None,
              open_days: int | None = None) -> dict:
    """One ordered request item. Unknown templates raise ValueError."""
    if template == "custom":
        return {"id": "custom", "template": "custom", "kind": "photo",
                "title": "One more thing", "instruction": (custom_text or "")[:500],
                "max_seconds": None, "custom": True, "open_until": None}
    spec = TEMPLATES[template]
    full = _catalogue.get(template)
    days = open_days
    if days is None and template == "screen_photo_next_time":
        days = int(os.environ.get("EYEWITNESS_NEXT_TIME_DAYS", "7"))
    open_until = int(time.time()) + days * 86400 if days else None
    item = {"id": template, "template": template, "kind": spec["kind"],
            "title": spec["title"], "instruction": spec["instruction"],
            "max_seconds": spec["max_seconds"], "custom": False,
            "open_until": open_until}
    # Only templates that define these get them, so items for the
    # older templates keep exactly the keys they always had.
    if full is not None:
        if full.get("record_label"):
            item["record_label"] = full["record_label"]
        retake = full.get("retake") or {}
        advice = {k: retake[k] for k in ("too_short", "too_quiet")
                  if retake.get(k)}
        if advice:
            item["phone_advice"] = advice
        if full.get("reader") == "beeps":
            item["raw_audio"] = True
    return item


def make_question_item(template: str) -> dict:
    """One quick question as a request item (kind question). The page
    posts its answer through the token answer route; it needs no file."""
    spec = QUESTIONS[template]
    return {"id": f"q_{template}", "template": template,
            "kind": "question", "title": spec["title"], "instruction": "",
            "options": list(spec.get("options") or []),
            "free_text_max": spec.get("free_text_max"),
            "max_seconds": None, "custom": False, "open_until": None}


def needs_approval(items: list[dict], *, existing_requests: int,
                   auto_send_templates: bool = True) -> bool:
    """Custom items and second requests always wait for a technician."""
    if any(i.get("custom") for i in items):
        return True
    if existing_requests >= 1:
        return True
    return not auto_send_templates


def render_customer_text(items: list[dict], link: str, expires_at: int,
                         msp_name: str = "Your IT support") -> str:
    """The only customer-bound wording: server-rendered from templates."""
    from datetime import datetime, timezone
    expiry = datetime.fromtimestamp(expires_at, timezone.utc).strftime("%d %b %Y")
    lines = [f"Hello, this is {msp_name}. To progress your ticket, please "
             "provide the following.", ""]
    for n, item in enumerate(items):
        if item.get("kind") == "question":
            opts = item.get("options") or []
            suffix = f" ({', '.join(opts)})" if opts else ""
            lines.append(f"{LETTERS[n]}. {item['title']}{suffix}")
        else:
            lines.append(f"{LETTERS[n]}. {item['title']}. {item['instruction']}")
    lines += ["", f"{LINK_LEAD}{link}",
              f"The link works until {expiry}. You can skip anything you "
              "can't do. Thank you."]
    return "\n".join(lines)


LINK_LEAD = "Open this link on your phone: "


def render_customer_html(text: str, items: list[dict] | None = None) -> str:
    """The vetted customer text as HTML, for helpdesks that show HTML.

    No wording is added or changed. Every line is escaped and becomes a
    paragraph, each item's own title is set in bold, and the link line
    becomes a link to the same address. The outgoing filter has already
    read this exact text, so the HTML says nothing it did not check.
    """
    titles = {LETTERS[n]: str(item.get("title") or "")
              for n, item in enumerate((items or [])[:len(LETTERS)])}
    out = []
    for line in (text or "").splitlines():
        if line.strip():
            out.append(_customer_line_html(line, titles))
    return "\n".join(out)


def _customer_line_html(line: str, titles: dict) -> str:
    esc = html.escape
    item = re.match(r"^([A-H])\. (.*)$", line)
    if item:
        letter, rest = item.group(1), item.group(2)
        title = titles.get(letter, "")
        if title and rest.startswith(title):
            return (f"<p><strong>{esc(letter)}. {esc(title)}</strong>"
                    f"{esc(rest[len(title):])}</p>")
    if line.startswith(LINK_LEAD):
        url = line[len(LINK_LEAD):].strip()
        if re.fullmatch(r"https?://\S+", url):
            return (f"<p><strong>{esc(LINK_LEAD.strip())}</strong><br>"
                    f'<a href="{esc(url, quote=True)}">{esc(url)}</a></p>')
    return f"<p>{esc(line)}</p>"


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_expired(expires_at: int, now: int | None = None) -> bool:
    return int(now if now is not None else time.time()) >= int(expires_at)


def link_ttl_seconds(next_time: bool = False) -> int:
    if next_time:
        return int(os.environ.get("EYEWITNESS_NEXT_TIME_DAYS", "7")) * 86400
    return int(os.environ.get("EYEWITNESS_LINK_TTL_HOURS", "72")) * 3600

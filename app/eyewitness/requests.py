"""Evidence templates and request lifecycle (brief sections 7.5-7.7).

Customer-facing text is rendered here from templates only. No model
text reaches a customer: `custom` items and any second request for the
same ticket wait for a technician (`needs_approval`).
"""
from __future__ import annotations

import os
import secrets
import time

# template -> kind, title, instruction, max_seconds, next-time capable
TEMPLATES: dict[str, dict] = {
    "screen_photo": {"kind": "photo", "max_seconds": None, "next_time": True,
                     "title": "Photograph the error on the screen",
                     "instruction": "Hold the phone about 30 cm from the screen, tap the screen to focus, and take the photo."},
    "screen_photo_next_time": {"kind": "photo", "max_seconds": None, "next_time": True,
                               "title": "Photograph the error when it happens again",
                               "instruction": "When the error appears again, open this link and photograph the screen."},
    "power_light_video": {"kind": "video", "max_seconds": 15, "next_time": False,
                          "title": "Film the power light",
                          "instruction": "Press the power button, then film the light for 10 seconds. Keep the phone still."},
    "sticker": {"kind": "photo", "max_seconds": None, "next_time": False,
                "title": "Photograph the sticker",
                "instruction": "Photograph the label or sticker on the back or underside of the device."},
    "whole_device": {"kind": "photo", "max_seconds": None, "next_time": False,
                     "title": "Photograph the whole device",
                     "instruction": "Take a photo showing the whole device and anything connected to it."},
    "cables_back": {"kind": "photo", "max_seconds": None, "next_time": False,
                    "title": "Photograph the cables at the back",
                    "instruction": "Photograph the cables plugged into the back of the device."},
    "printer_panel": {"kind": "photo", "max_seconds": None, "next_time": False,
                      "title": "Photograph the printer panel",
                      "instruction": "Photograph the printer display so the message can be read."},
    "voice_note": {"kind": "voice", "max_seconds": 60, "next_time": False,
                   "title": "Tell us what happened",
                   "instruction": "Record a short voice note describing what happened, in your own words."},
}

QUESTIONS: dict[str, dict] = {
    "since_when": {"title": "When did it start?",
                   "options": ["today", "this week", "longer", "can't say"]},
    "how_often": {"title": "How often does it happen?",
                  "options": ["once", "several times", "constantly"]},
    "what_changed": {"title": "Did anything change just before it started?",
                     "options": [], "free_text_max": 200},
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
    days = open_days
    if days is None and template == "screen_photo_next_time":
        days = int(os.environ.get("EYEWITNESS_NEXT_TIME_DAYS", "7"))
    open_until = int(time.time()) + days * 86400 if days else None
    return {"id": template, "template": template, "kind": spec["kind"],
            "title": spec["title"], "instruction": spec["instruction"],
            "max_seconds": spec["max_seconds"], "custom": False,
            "open_until": open_until}


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
    lines += ["", f"Open this link on your phone: {link}",
              f"The link works until {expiry}. You can skip anything you "
              "can't do. Thank you."]
    return "\n".join(lines)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_expired(expires_at: int, now: int | None = None) -> bool:
    return int(now if now is not None else time.time()) >= int(expires_at)


def link_ttl_seconds(next_time: bool = False) -> int:
    if next_time:
        return int(os.environ.get("EYEWITNESS_NEXT_TIME_DAYS", "7")) * 86400
    return int(os.environ.get("EYEWITNESS_LINK_TTL_HOURS", "72")) * 3600

"""Guide photos (S2): a mark drawn on the customer photo.

Technician triggered only, never automatic. Muse Image draws the
circle or arrow where the prompt puts it; where the edit endpoint
refuses, Pillow draws the same mark at the centre and the reading
says so. The result joins a new request that waits for approval.
"""
from __future__ import annotations

import io

PROMPTS = {
    "circle": "Draw one thick red circle around the power button "
              "on this device photo. Keep the photo itself "
              "unchanged. Add no text and no other marks.",
    "arrow": "Draw one thick red arrow pointing at the power "
             "button on this device photo. Keep the photo itself "
             "unchanged. Add no text and no other marks.",
}


def _pillow_guide(data: bytes, style: str) -> bytes:
    from PIL import Image, ImageDraw
    with Image.open(io.BytesIO(data)) as img:
        base = img.convert("RGB")
    draw = ImageDraw.Draw(base)
    w, h = base.size
    cx, cy = w // 2, h // 2
    r = max(min(w, h) // 6, 20)
    width = max(r // 5, 4)
    if style == "arrow":
        draw.line([(cx - 2 * r, cy - 2 * r), (cx, cy)],
                  fill=(230, 30, 30), width=width)
        draw.polygon([(cx, cy), (cx - width * 3, cy + width),
                      (cx + width, cy - width * 3)],
                     fill=(230, 30, 30))
    else:
        draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                     outline=(230, 30, 30), width=width)
    out = io.BytesIO()
    base.save(out, format="JPEG", quality=88)
    return out.getvalue()


def make_guide(data: bytes, style: str, *, client=None,
               capture_id=None, intake_run_id=None) -> dict:
    """{bytes, method} for one guide photo."""
    style = style if style in PROMPTS else "circle"
    if client is not None and client.is_configured:
        try:
            made = client.image_edit(
                data, PROMPTS[style], capture_id=capture_id,
                intake_run_id=intake_run_id)
            if made:
                return {"bytes": made, "method": "muse-image"}
        except Exception:
            pass
    return {"bytes": _pillow_guide(data, style),
            "method": "local-draw"}

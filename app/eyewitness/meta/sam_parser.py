"""Parser for SAM 3.1 streamed output.

Wire format (measured live, Phase 1 spike): the Responses SSE stream's
final message text carries one segment per object like
`<0f>0<|box;x1=969;y1=639;x2=1150;y2=819;w=1280;h=960|><|mask;x=0;y=0;data=...|>`
where the leading `<0f>N` token is the object id (stable across video
frames) and mask data starting with `~` would mean lossless. This
module is pure: text in, objects out, tested without any API call.
The older `<box>..</box>` spelling is accepted too.
"""
from __future__ import annotations

import json
import re

_BOX = re.compile(r"<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)\|>")
_BOX_ALT = re.compile(r"<box>\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*</box>")
_MASK = re.compile(r"<\|mask;x=(\d+);y=(\d+);data=([^|]+)\|>")
_MASK_ALT = re.compile(r"<mask>\s*(\d+)\s+(\d+)\s+(\S+)\s*</mask>")
_OBJ = re.compile(r"<0f>(\d+)")


def extract_sam_text(sse_text: str) -> str:
    """The final message text out of a Responses SSE stream.

    Plain (non-SSE) text passes through unchanged.
    """
    done_text = None
    for line in (sse_text or "").splitlines():
        if not line.startswith("data: {"):
            continue
        try:
            event = json.loads(line[6:])
        except ValueError:
            continue
        if event.get("type") == "response.output_text.done":
            done_text = event.get("text") or done_text
        if event.get("type") == "response.completed":
            for item in (event.get("response") or {}).get("output") or []:
                for part in item.get("content") or []:
                    if part.get("text"):
                        return part["text"]
    return done_text if done_text is not None else (sse_text or "")


def parse_sam_text(text: str) -> list[dict]:
    """Parse one frame/response text into objects.

    Returns [{id, box:{x1,y1,x2,y2,w,h}, mask:{x,y,data,lossless}|None}].
    """
    text = extract_sam_text(text or "")
    objects: list[dict] = []
    # Split on object markers, keeping order; text before the first
    # marker still parses with sequential ids.
    chunks = _OBJ.split(text)
    # chunks: [pre, id, body, id, body, ...] when markers exist.
    segments: list[tuple[int | None, str]] = []
    if len(chunks) > 1:
        if chunks[0].strip():
            segments.append((None, chunks[0]))
        for i in range(1, len(chunks), 2):
            body = chunks[i + 1] if i + 1 < len(chunks) else ""
            segments.append((int(chunks[i]), body))
    else:
        segments.append((None, text))
    for seg_id, body in segments:
        for box_match in list(_BOX.finditer(body)) or list(_BOX_ALT.finditer(body)):
            vals = [int(v) for v in box_match.groups()]
            obj = {"id": seg_id if seg_id is not None else len(objects),
                   "box": {"x1": vals[0], "y1": vals[1], "x2": vals[2],
                           "y2": vals[3], "w": vals[4], "h": vals[5]},
                   "mask": None}
            tail = body[box_match.end():]
            mask_match = _MASK.search(tail) or _MASK_ALT.search(tail)
            if mask_match:
                data = mask_match.group(3)
                obj["mask"] = {"x": int(mask_match.group(1)),
                               "y": int(mask_match.group(2)),
                               "data": data.lstrip("~"),
                               "lossless": data.startswith("~")}
            objects.append(obj)
    return objects


def parse_sam_frames(texts: list[str]) -> list[list[dict]]:
    """Parse a video response split per frame, preserving frame order."""
    return [parse_sam_text(t) for t in (texts or [])]

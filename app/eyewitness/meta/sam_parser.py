"""Parser for SAM 3.1 streamed output.

Wire format (measured live, Phase 1 spike): the Responses SSE stream's
final message text carries one segment per object like
`<0f>0<|box;x1=969;y1=639;x2=1150;y2=819;w=1280;h=960|><|mask;x=0;y=0;data=...|>`
where the leading `<Nf>` token is the frame index (0 for a still image)
and the digits after it are the object id. For video there is one
output_text delta per frame; a frame with no visible object produces
no line, so frame indices skip. Records in one frame are separated by
commas and a record may carry a confidence as `;c=` in the box or
mask. Mask data starting with `~` would mean lossless, and for video
the payload is `H,W,!base85`. This module is pure: text in, objects
out, tested without any API call. The older `<box>..</box>` spelling
is accepted too.
"""
from __future__ import annotations

import json
import re

_BOX = re.compile(
    r"<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)"
    r"(?:;c=[^|]*)?\|>")
_BOX_ALT = re.compile(
    r"<box>\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*</box>")
_MASK = re.compile(
    r"<\|mask;x=(\d+);y=(\d+)(?:;c=[^;|]+)?;data=([^|]+)\|>")
_MASK_ALT = re.compile(r"<mask>\s*(\d+)\s+(\d+)\s+(\S+)\s*</mask>")
_FRAME = re.compile(r"<(\d+)f>")
_OBJ = re.compile(r"<0f>(\d+)")


def extract_sam_text(sse_text: str) -> str:
    """The final message text out of a Responses SSE stream.

    Plain (non-SSE) text passes through unchanged. Deltas are joined
    when no done or completed text is present, which is how a video
    stream that was read incrementally still parses.
    """
    done_text = None
    deltas: list[str] = []
    for line in (sse_text or "").splitlines():
        if not line.startswith("data: {"):
            continue
        try:
            event = json.loads(line[6:])
        except ValueError:
            continue
        if event.get("type") == "response.output_text.delta":
            if event.get("delta"):
                deltas.append(str(event["delta"]))
        if event.get("type") == "response.output_text.done":
            done_text = event.get("text") or done_text
        if event.get("type") == "response.completed":
            for item in (event.get("response") or {}).get("output") or []:
                for part in item.get("content") or []:
                    if part.get("text"):
                        return part["text"]
    if done_text is not None:
        return done_text
    if deltas:
        return "".join(deltas)
    return sse_text or ""


def _objects_in_segment(body: str) -> list[dict]:
    """Objects in one frame segment (no frame header inside)."""
    objects: list[dict] = []
    boxes = list(_BOX.finditer(body)) or list(_BOX_ALT.finditer(body))
    for box_match in boxes:
        vals = [int(v) for v in box_match.groups()]
        # The object id is the digits directly before the box record.
        prefix = body[max(0, box_match.start() - 10):box_match.start()]
        found = re.search(r"(\d+)\s*,?\s*$", prefix)
        obj_id = int(found.group(1)) if found else len(objects)
        obj = {"id": obj_id,
               "box": {"x1": vals[0], "y1": vals[1], "x2": vals[2],
                       "y2": vals[3], "w": vals[4], "h": vals[5]},
               "mask": None}
        tail = body[box_match.end():]
        # The mask belongs to this box only up to the next box.
        nxt = _BOX.search(tail)
        scope = tail[:nxt.start()] if nxt else tail
        mask_match = _MASK.search(scope) or _MASK_ALT.search(scope)
        if mask_match:
            data = mask_match.group(3)
            obj["mask"] = {"x": int(mask_match.group(1)),
                           "y": int(mask_match.group(2)),
                           "data": data.lstrip("~"),
                           "lossless": data.startswith("~")}
        objects.append(obj)
    return objects


def parse_sam_text(text: str) -> list[dict]:
    """Parse one frame or response text into objects.

    Returns [{id, box:{x1,y1,x2,y2,w,h}, mask:{x,y,data,lossless}|None}].
    For a video text the objects from every frame are returned in
    frame order; use parse_sam_video when the frame split matters.
    """
    text = extract_sam_text(text or "")
    frames = parse_sam_video(text)
    if frames:
        out: list[dict] = []
        for index in sorted(frames):
            out.extend(frames[index])
        return out
    # Legacy path with no frame header: the whole text is one frame.
    return _objects_in_segment(text)


def parse_sam_video(text: str) -> dict[int, list[dict]]:
    """Parse a SAM video text into {frame_index: [objects]}.

    Frames with no visible object produce no line and are absent from
    the result, so the keys skip. An empty result means zero objects,
    which ends with response.completed and empty text.
    """
    text = extract_sam_text(text or "")
    parts = _FRAME.split(text or "")
    # parts: [pre, frame, body, frame, body, ...] when headers exist.
    if len(parts) <= 1:
        return {}
    frames: dict[int, list[dict]] = {}
    # Text before the first header holds no frame objects.
    for i in range(1, len(parts), 2):
        try:
            index = int(parts[i])
        except (TypeError, ValueError):
            continue
        body = parts[i + 1] if i + 1 < len(parts) else ""
        objs = _objects_in_segment(body)
        if objs:
            frames.setdefault(index, []).extend(objs)
    return frames


def union_box(frames: dict[int, list[dict]]) -> dict | None:
    """The union of every box in a video result, or None.

    A blinking light is dark on its off frames and may return under a
    new id, so the reader fixes one region from the union and then
    measures that region on every decoded frame in code.
    """
    boxes = [o["box"] for objs in (frames or {}).values() for o in objs
             if o.get("box")]
    if not boxes:
        return None
    return {"x1": min(b["x1"] for b in boxes),
            "y1": min(b["y1"] for b in boxes),
            "x2": max(b["x2"] for b in boxes),
            "y2": max(b["y2"] for b in boxes),
            "w": boxes[0]["w"], "h": boxes[0]["h"]}


def parse_sam_frames(texts: list[str]) -> list[list[dict]]:
    """Parse a video response split per frame, preserving frame order."""
    return [parse_sam_text(t) for t in (texts or [])]

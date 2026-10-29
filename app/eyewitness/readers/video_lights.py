"""Blinking light reader (brief 7.3, Phase 3 live).

SAM 3.1 locates the light, code counts the flashes. SAM ids are not
stable across an off period, so the reader fixes one region from the
union of the boxes SAM returns and then measures brightness and hue
inside that region on every decoded frame. When SAM finds nothing
the fallback locator is code as well: the small region whose
brightness changes most over time. The counting is always code.

Retake advice never comes from a model. The reader returns a reason
from RETAKE_ADVICE and the customer reads the fixed sentence for it.
"""
from __future__ import annotations

import json
import os
import statistics

_DELL = None

PROMPT_VERSION = "video_describe_v1"

# One concept prompt. On the synthetic clips no concept found the light
# reliably, while a point prompt on the code-found candidate tracked it
# on every lit frame (28 Oct), so the point prompt does the following.
CONCEPTS = ("power button light",)

MIN_DURATION_S = 3.0
MAX_DURATION_S = 20.0
DARK_MEAN = 12.0
DARK_HIGHLIGHT = 40.0
MOVEMENT_MAX = 18.0
MIN_BLINK_RANGE = 10.0
# The fallback locator's bar: the spread of a cell's mean brightness
# once frames are lined up. A small blinking LED measured 17; a shaken
# scene with no light measured under 4 (synthetic clips, 28 Oct).
MIN_CELL_RANGE = 8.0

RETAKE_ADVICE = {
    "too_short": "The clip is too short. Film the light for the full "
                 "time shown on the item, then try again.",
    "too_dark": "The video is too dark to see anything. Turn on a "
                "light, then try again.",
    "light_not_found": "No light could be found in the video. Fill "
                       "the frame with the power light and keep the "
                       "phone still, then try again.",
    "too_much_movement": "The phone moved too much to follow the "
                         "light. Rest the phone on something steady, "
                         "then try again.",
}

DESCRIBE_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "light_visible": {"type": "boolean"},
        "instruction_like_text": {"type": "boolean"},
        "instruction_excerpt": {"type": ["string", "null"]},
    },
    "required": ["description", "light_visible",
                 "instruction_like_text", "instruction_excerpt"],
    "additionalProperties": False,
}


def _retake(reason: str, reading: dict | None = None) -> dict:
    return {"verdict": "retake",
            "retake": {"reason": reason,
                       "advice": RETAKE_ADVICE[reason]},
            "reading": reading or {"prompt_version": PROMPT_VERSION},
            "injection_flag": False}


def dell_table() -> dict:
    global _DELL
    if _DELL is None:
        path = os.path.join(os.path.dirname(__file__), "..", "decode",
                            "dell_led.json")
        with open(path, encoding="utf-8") as fh:
            _DELL = json.load(fh)
    return _DELL


def group_bursts(events: list[dict]) -> list[dict]:
    """Group (colour, gap_after_s) events into bursts.

    events: [{colour, on_s, gap_after_s}] in time order. A gap longer
    than 1.5x the median gap starts a new burst.
    """
    if not events:
        return []
    gaps = [e["gap_after_s"] for e in events[:-1]
            if e.get("gap_after_s") is not None]
    median = statistics.median(gaps) if gaps else 0.0
    bursts: list[dict] = []
    current = {"colour": events[0]["colour"], "count": 1}
    for prev, nxt in zip(events, events[1:]):
        gap = prev.get("gap_after_s") or 0.0
        if median and gap > 1.5 * median \
                and nxt["colour"] == current["colour"]:
            bursts.append(current)
            current = {"colour": nxt["colour"], "count": 1}
        elif nxt["colour"] != current["colour"]:
            bursts.append(current)
            current = {"colour": nxt["colour"], "count": 1}
        else:
            current["count"] += 1
    bursts.append(current)
    return bursts


def decode_dell(bursts: list[dict]) -> dict | None:
    """Dell amber/white pattern -> meaning, or None when unknown."""
    table = dell_table().get("patterns", {})
    for burst_pair in _pairs(bursts):
        key = f"amber:{burst_pair[0]} white:{burst_pair[1]}"
        if key in table:
            return {"pattern": key, "meaning": table[key],
                    "source": "dell_led.json", "estimate": False}
    return None


def _pairs(bursts: list[dict]) -> list[tuple[int, int]]:
    pairs = []
    for i in range(len(bursts) - 1):
        a, b = bursts[i], bursts[i + 1]
        if a["colour"] == "amber" and b["colour"] == "white":
            pairs.append((a["count"], b["count"]))
    return pairs


def bursts_text(bursts: list[dict]) -> str:
    """`amber 2, white 3` for the summary note and the ticket view."""
    return ", ".join(f"{b['colour']} {b['count']}" for b in bursts)


def classify_colour(hue: float, sat: float) -> str:
    """One bright pixel group to a Dell colour band (OpenCV hue)."""
    if sat < 70:
        return "white"
    if 8 <= hue <= 28:
        return "amber"
    return "other"


def flashes_from_series(series: list[dict], fps: float) -> list[dict]:
    """On/off flashes from per-frame brightness and colour, in code.

    Hysteresis: a flash starts over the high mark and ends under the
    low mark, both set from the series range, so a steady light and
    sensor noise make no flashes.
    """
    if not series or fps <= 0:
        return []
    values = [float(s["brightness"]) for s in series]
    low_bg = float(statistics.quantiles(values, n=20)[0]) \
        if len(values) >= 5 else min(values)
    high_peak = float(statistics.quantiles(values, n=20)[-1]) \
        if len(values) >= 5 else max(values)
    span = high_peak - low_bg
    if span < MIN_BLINK_RANGE:
        return []
    on_mark = low_bg + 0.60 * span
    off_mark = low_bg + 0.35 * span
    flashes: list[dict] = []
    start: int | None = None
    for i, frame in enumerate(series):
        bright = float(frame["brightness"])
        if start is None and bright > on_mark:
            start = i
        elif start is not None and bright < off_mark:
            flashes.append(_flash(series, start, i, fps))
            start = None
    if start is not None and len(series) - start >= 2:
        flashes.append(_flash(series, start, len(series), fps))
    flashes = [f for f in flashes if f["frames"] >= 2]
    for i, flash in enumerate(flashes):
        nxt = flashes[i + 1]["start_s"] if i + 1 < len(flashes) else None
        flash["gap_after_s"] = (round(nxt - flash["end_s"], 3)
                                if nxt is not None else None)
    events = [{"colour": f["colour"], "on_s": f["on_s"],
               "gap_after_s": f["gap_after_s"]} for f in flashes]
    return events


def _flash(series, start: int, end: int, fps: float) -> dict:
    colours = [s.get("colour") for s in series[start:end]
               if s.get("colour") in ("amber", "white")]
    colour = statistics.mode(colours) if colours else "white"
    return {"colour": colour, "start_s": round(start / fps, 3),
            "end_s": round(end / fps, 3),
            "on_s": round((end - start) / fps, 3), "frames": end - start}


def video_quality(*, duration_s: float, mean_brightness: float,
                  highlight: float, movement: float) -> str | None:
    """The fixed retake reason for a normalised clip, or None."""
    if duration_s < MIN_DURATION_S:
        return "too_short"
    if mean_brightness < DARK_MEAN and highlight < DARK_HIGHLIGHT:
        return "too_dark"
    if movement > MOVEMENT_MAX:
        return "too_much_movement"
    return None


# -- frame measurement (code, never a model) ---------------------------

def measure_region_series(frames, box: dict) -> list[dict]:
    """Per-frame brightness and colour inside one fixed region.

    `frames` are BGR images (OpenCV order) and `box` is in their pixel
    coordinates. Colour comes from the bright pixels only, so the dark
    panel around a small light does not wash the band out.
    """
    import cv2
    import numpy as np
    x1, y1 = max(int(box["x1"]), 0), max(int(box["y1"]), 0)
    series: list[dict] = []
    for frame in frames:
        h, w = frame.shape[:2]
        x2, y2 = min(int(box["x2"]), w), min(int(box["y2"]), h)
        if x2 <= x1 or y2 <= y1:
            series.append({"brightness": 0.0, "colour": "other"})
            continue
        crop = frame[y1:y2, x1:x2]
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        value = hsv[:, :, 2].astype(np.float64)
        bright = value > 140
        colour = "other"
        if int(bright.sum()) >= 3:
            hue = float(np.median(hsv[:, :, 0][bright]))
            sat = float(np.median(hsv[:, :, 1][bright]))
            colour = classify_colour(hue, sat)
        # A small light fills little of its region, so the signal is
        # the brightest tenth, not the mean over the dark panel.
        cut = np.percentile(value, 90) if value.size else 0.0
        top = value[value >= cut]
        series.append({"brightness": float(top.mean())
                       if top.size else 0.0,
                       "colour": colour})
    return series


def point_prompt(frames, region: dict) -> str:
    """A SAM point prompt on a code-found candidate: its centre, on the
    frame where the candidate is brightest. Coordinates stay inside the
    frame and w/h are the frame's own, as SAM requires."""
    import numpy as np
    x1, y1, x2, y2 = (int(region[k]) for k in ("x1", "y1", "x2", "y2"))
    levels = [float(np.asarray(f)[y1:y2, x1:x2].mean()) for f in frames]
    n = int(np.argmax(levels)) if levels else 0
    h, w = np.asarray(frames[0]).shape[:2]
    cx = min(max((x1 + x2) // 2, 0), w - 1)
    cy = min(max((y1 + y2) // 2, 0), h - 1)
    return f"<{n}f>1<|point;x={cx};y={cy};w={w};h={h}|>"


def _sam_found(client, text: str, media_part: dict, frame_count: int,
               capture_id=None, intake_run_id=None) -> dict:
    """SAM frames for one prompt, or {} when the call or the parse fails."""
    from app.eyewitness.meta.sam_parser import parse_sam_video
    try:
        raw = client.sam_segment(concept=text, media_part=media_part,
                                 capture_id=capture_id,
                                 intake_run_id=intake_run_id,
                                 frame_count=frame_count)
        return parse_sam_video(raw) or {}
    except Exception:
        return {}


def follow_candidate(frames, code_region: dict, *, client, media_part,
                     frame_count: int, capture_id=None,
                     intake_run_id=None):
    """SAM follows the code-found candidate from a point prompt.

    Returns (region, prompt, sam_frames) with the region expanded
    around SAM's boxes, or None when SAM tracks nothing there.
    """
    from app.eyewitness.meta.sam_parser import union_box
    prompt = point_prompt(frames, code_region)
    found = _sam_found(client, prompt, media_part, frame_count,
                       capture_id, intake_run_id)
    box = union_box(found)
    if box is None:
        return None
    h, w = frames[0].shape[:2]
    return _expand(_scale_box(box, w, h), w, h), prompt, found


def _sam_media_part(client, norm_path: str, capture_id=None,
                    intake_run_id=None) -> dict:
    """The normalised clip as one input_video part, built once per read:
    inline up to 6 MB, otherwise one upload to the files endpoint."""
    with open(norm_path, "rb") as fh:
        norm_bytes = fh.read()
    if len(norm_bytes) <= 6 * 1024 * 1024:
        import base64
        return {"type": "input_video",
                "video_url": "data:video/mp4;base64,"
                + base64.b64encode(norm_bytes).decode()}
    return {"type": "input_video",
            "file_id": client.upload_file(norm_bytes, "clip.mp4",
                                          capture_id=capture_id,
                                          intake_run_id=intake_run_id)}


def _stabilise(stack):
    """Line every frame up with the first (translation only).

    Hand shake moves every edge in the picture, and a moving edge
    looks like a changing light. Phase correlation measures each
    frame's shift and the frame is moved back. OpenCV windows its
    inputs in place, so it gets copies.
    """
    import cv2
    import numpy as np
    ref = stack[0].astype(np.float32)
    window = cv2.createHanningWindow((ref.shape[1], ref.shape[0]),
                                     cv2.CV_32F)
    out = [ref.copy()]
    for frame in stack[1:]:
        cur = frame.astype(np.float32)
        (dx, dy), _ = cv2.phaseCorrelate(ref.copy(), cur.copy(), window)
        shift = np.float32([[1, 0, -dx], [0, 1, -dy]])
        out.append(cv2.warpAffine(cur, shift, (cur.shape[1], cur.shape[0]),
                                  borderMode=cv2.BORDER_REPLICATE))
    return np.stack(out).astype(np.float64)


def locate_by_motion(grey_frames, *, grid_rows: int = 12,
                     grid_cols: int = 16) -> dict | None:
    """The grid cell whose mean brightness changes most over time.

    The code-only fallback locator. Frames are lined up first, then
    each interior cell is scored on the spread of its mean brightness
    less the frame's mean (which takes out exposure drift). A light
    going on and off moves its cell's mean; texture under hand shake
    moves single pixels and leaves the mean almost still. The outer
    ring of cells is left out, because lining up shifts the borders.
    Returns a box in the grey frames' pixel coordinates with its
    range, or None when nothing changes enough to be a blinking light.
    """
    import numpy as np
    if len(grey_frames) < 5:
        return None
    stack = _stabilise(np.stack([np.asarray(f) for f in grey_frames]))
    frame_mean = stack.mean(axis=(1, 2))
    h, w = stack.shape[1:]
    best = None
    for r in range(1, grid_rows - 1):
        for c in range(1, grid_cols - 1):
            ys, ye = int(r * h / grid_rows), int((r + 1) * h / grid_rows)
            xs, xe = int(c * w / grid_cols), int((c + 1) * w / grid_cols)
            cell = stack[:, ys:ye, xs:xe]
            if not cell.size:
                continue
            series = cell.mean(axis=(1, 2)) - frame_mean
            score = float(np.percentile(series, 95)
                          - np.percentile(series, 5))
            if best is None or score > best["range"]:
                best = {"x1": xs, "y1": ys, "x2": xe, "y2": ye,
                        "w": int(w), "h": int(h), "range": score}
    if best is None or best["range"] < MIN_CELL_RANGE:
        return None
    # Tighten the winning cell to the pixel that changes most, so the
    # counting region holds the light and little of the panel.
    spread = np.percentile(stack, 95, axis=0) \
        - np.percentile(stack, 5, axis=0)
    cell_spread = spread[best["y1"]:best["y2"], best["x1"]:best["x2"]]
    peak_y, peak_x = np.unravel_index(int(np.argmax(cell_spread)),
                                      cell_spread.shape)
    cx, cy = best["x1"] + int(peak_x), best["y1"] + int(peak_y)
    half = max(min((best["x2"] - best["x1"]),
                   (best["y2"] - best["y1"])) // 4, 4)
    best = {**best, "x1": max(cx - half, 0), "y1": max(cy - half, 0),
            "x2": min(cx + half, w), "y2": min(cy + half, h)}
    return best


def _expand(box: dict, frame_w: int, frame_h: int,
            pad_frac: float = 0.35) -> dict:
    bw = max(int(box["x2"]) - int(box["x1"]), 4)
    bh = max(int(box["y2"]) - int(box["y1"]), 4)
    pad_x, pad_y = int(bw * pad_frac) + 2, int(bh * pad_frac) + 2
    return {"x1": max(int(box["x1"]) - pad_x, 0),
            "y1": max(int(box["y1"]) - pad_y, 0),
            "x2": min(int(box["x2"]) + pad_x, frame_w),
            "y2": min(int(box["y2"]) + pad_y, frame_h),
            "w": frame_w, "h": frame_h}


def _scale_box(box: dict, frame_w: int, frame_h: int) -> dict:
    """A SAM box from its own w/h space into the decoded frame size."""
    sw, sh = int(box.get("w") or frame_w), int(box.get("h") or frame_h)
    if sw == frame_w and sh == frame_h:
        return {**box, "w": frame_w, "h": frame_h}
    kx, ky = frame_w / max(sw, 1), frame_h / max(sh, 1)
    return {"x1": int(int(box["x1"]) * kx),
            "y1": int(int(box["y1"]) * ky),
            "x2": int(int(box["x2"]) * kx),
            "y2": int(int(box["y2"]) * ky),
            "w": frame_w, "h": frame_h}


# -- ffmpeg and decoding ------------------------------------------------

def _run_ffmpeg(args: list[str], timeout: int = 90) -> None:
    import subprocess
    done = subprocess.run(args, capture_output=True, timeout=timeout)
    if done.returncode != 0:
        raise RuntimeError("ffmpeg step failed")


def probe_video(path: str) -> dict:
    """Duration, size and frame rate from ffprobe (no network)."""
    import subprocess
    done = subprocess.run(
        ["ffprobe", "-v", "error", "-protocol_whitelist", "file",
         "-select_streams", "v:0",
         "-show_entries",
         "format=duration:stream=width,height,avg_frame_rate",
         "-of", "json", path], capture_output=True, text=True,
        timeout=30)
    if done.returncode != 0:
        raise RuntimeError("ffprobe failed")
    info = json.loads(done.stdout or "{}")
    stream = (info.get("streams") or [{}])[0]
    fps = 0.0
    rate = str(stream.get("avg_frame_rate") or "0/1")
    if "/" in rate:
        top, _, bottom = rate.partition("/")
        try:
            fps = float(top) / max(float(bottom), 1.0)
        except ValueError:
            fps = 0.0
    return {"duration_s": float(
                (info.get("format") or {}).get("duration") or 0),
            "width": int(stream.get("width") or 0),
            "height": int(stream.get("height") or 0), "fps": fps}


def normalise_video(src_path: str, dst_path: str) -> dict:
    """Transcode any upload to H.264 MP4 at 10 fps with faststart.

    VP9 and WebM are refused by SAM, so every clip goes through this
    step. No audio is kept here; the beep reader reads the source.
    Returns the probe of the normalised file.
    """
    # Only the local file protocol, and at most MAX_DURATION_S of the
    # clip: the frames held in memory and the SAM frames billed both
    # stay bounded, whatever length a file upload arrives with.
    _run_ffmpeg(["ffmpeg", "-y", "-nostdin", "-protocol_whitelist", "file",
                 "-i", src_path, "-t", str(MAX_DURATION_S),
                 "-vf", "fps=10,scale='min(1280,iw)':-2",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-movflags", "+faststart", "-an", dst_path])
    return probe_video(dst_path)


def decode_frames(path: str):
    """Every frame of the normalised clip as BGR images plus fps."""
    import cv2
    cap = cv2.VideoCapture(path)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 10.0)
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    return frames, (fps if fps > 0 else 10.0)


def clip_stats(frames) -> dict:
    """Mean brightness, highlight and movement over decoded frames."""
    import cv2
    import numpy as np
    if not frames:
        return {"mean_brightness": 0.0, "highlight": 0.0,
                "movement": 0.0}
    greys = []
    for frame in frames[::3]:
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(grey, (160, 120))
        greys.append(small.astype(np.float64))
    stack = np.stack(greys)
    movement = float(np.mean(np.abs(np.diff(stack, axis=0)))) \
        if len(greys) > 1 else 0.0
    return {"mean_brightness": float(stack.mean()),
            "highlight": float(np.percentile(stack, 99.9)),
            "movement": movement}


def _load_describe_prompt() -> str:
    path = os.path.join(os.path.dirname(__file__), "..", "prompts",
                        "video_describe_v1.md")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _describe_keyframes(frames, client, capture_id, intake_run_id):
    """One Spark call on three keyframes. Returns (text, flag) or
    (None, False). The text stays inside the ticket fence."""
    if client is None or not frames:
        return None, False
    import base64
    import cv2
    picks = [frames[len(frames) // 4], frames[len(frames) // 2],
             frames[(3 * len(frames)) // 4]]
    parts = [{"type": "text",
              "text": "Describe what visibly happens in this clip."}]
    for frame in picks:
        ok, buf = cv2.imencode(".jpg", frame,
                               [cv2.IMWRITE_JPEG_QUALITY, 82])
        if not ok:
            return None, False
        url = "data:image/jpeg;base64," + base64.b64encode(
            bytes(buf)).decode()
        parts.append({"type": "image_url",
                      "image_url": {"url": url}})
    try:
        raw = client.spark_json(system=_load_describe_prompt(),
                                user_content=parts,
                                schema=DESCRIBE_SCHEMA, kind="spark",
                                capture_id=capture_id,
                                intake_run_id=intake_run_id)
    except Exception:
        return None, False
    text = str(raw.get("description") or "")[:300] or None
    return text, bool(raw.get("instruction_like_text"))


def read_video(data: bytes, mime: str | None = None, *, client=None,
               capture_id=None, intake_run_id=None,
               tenant_uuid=None) -> dict:
    """Reader contract for one power light clip. The work folder holds
    copies of customer evidence, so it goes when the read ends."""
    import shutil
    import tempfile
    work = tempfile.mkdtemp(prefix="eyewitness-video-")
    try:
        return _read_video(data, mime, work=work, client=client,
                           capture_id=capture_id,
                           intake_run_id=intake_run_id,
                           tenant_uuid=tenant_uuid)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _read_video(data: bytes, mime: str | None = None, *, work: str,
                client=None, capture_id=None,
                intake_run_id=None, tenant_uuid=None) -> dict:
    """One power light clip, read inside `work`.

    Normalise with ffmpeg, then locate the light: one SAM concept
    prompt, or a code-found candidate that SAM follows from a point
    prompt, or code alone. Count in code on every frame, then decode
    with the Dell table. Beeps from the audio track join
    the reading when the clip carries them (S3).
    """
    if not data:
        return _retake("light_not_found")
    if client is None:
        try:
            from app.eyewitness.meta.client import reader_client
            client = reader_client(tenant_uuid)
        except Exception:
            client = None
    live_client = client if (client is not None
                             and client.is_configured) else None
    # Local mode has no SAM and no file upload. The code locator
    # finds the light on its own, code counts, and only the three
    # keyframes go to Glimmer for the description.
    sam_client = live_client if (live_client is not None and getattr(
        live_client, "supports_sam", True)) else None
    src = os.path.join(work, "source.bin")
    norm = os.path.join(work, "normalised.mp4")
    with open(src, "wb") as fh:
        fh.write(data)
    try:
        probe = probe_video(src)
    except Exception:
        return _retake("light_not_found")
    # A clip filmed through Chrome's recorder carries no duration in its
    # header, so an unknown length (0) goes on to normalisation, which
    # writes a real one; video_quality then checks the normalised clip.
    if 0 < probe["duration_s"] < MIN_DURATION_S:
        return _retake("too_short",
                       {"duration_s": probe["duration_s"],
                        "prompt_version": PROMPT_VERSION})
    try:
        norm_probe = normalise_video(src, norm)
        frames, fps = decode_frames(norm)
    except Exception:
        return _retake("light_not_found")
    if not frames:
        return _retake("light_not_found")
    frame_h, frame_w = frames[0].shape[:2]
    stats = clip_stats(frames)
    reason = video_quality(duration_s=norm_probe["duration_s"],
                           mean_brightness=stats["mean_brightness"],
                           highlight=stats["highlight"],
                           movement=stats["movement"])
    if reason:
        return _retake(reason,
                       {"duration_s": norm_probe["duration_s"],
                        "stats": stats,
                        "prompt_version": PROMPT_VERSION})
    # Locate. One SAM concept prompt first. Where it finds nothing, code
    # finds the candidate and SAM follows it from a point prompt. Code
    # alone is the last step. The counting is always code.
    region = None
    locator = None
    concept_used = None
    sam_frames: dict = {}
    frame_count = int(round(norm_probe["duration_s"] * 10))
    media_part = None
    if sam_client is not None:
        try:
            media_part = _sam_media_part(sam_client, norm, capture_id,
                                         intake_run_id)
        except Exception:
            media_part = None
    sam_file_id = (media_part or {}).get("file_id") if media_part else None

    def _cleanup_sam_file():
        if sam_file_id and sam_client is not None:
            try:
                deleter = getattr(sam_client, "delete_file", None)
                if deleter is not None:
                    deleter(sam_file_id)
            except Exception:
                pass

    if media_part is not None:
        from app.eyewitness.meta.sam_parser import union_box
        for concept in CONCEPTS:
            found = _sam_found(sam_client, concept, media_part,
                               frame_count, capture_id, intake_run_id)
            box = union_box(found)
            if box is None:
                continue
            sam_frames = found
            region = _expand(_scale_box(box, frame_w, frame_h),
                             frame_w, frame_h)
            locator, concept_used = "sam", concept
            break
    def code_then_point():
        """Code finds the candidate; SAM follows it when it can."""
        import cv2
        greys = [cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY),
                            (320, 240)) for f in frames[::2]]
        found = locate_by_motion(greys)
        if found is None:
            return None
        kx, ky = frame_w / 320.0, frame_h / 240.0
        code_region = _expand(
            {"x1": int(found["x1"] * kx), "y1": int(found["y1"] * ky),
             "x2": int(found["x2"] * kx), "y2": int(found["y2"] * ky),
             "w": frame_w, "h": frame_h}, frame_w, frame_h)
        if media_part is not None:
            followed = follow_candidate(
                frames, code_region, client=sam_client,
                media_part=media_part, frame_count=frame_count,
                capture_id=capture_id, intake_run_id=intake_run_id)
            if followed is not None:
                followed_region, prompt, followed_frames = followed
                return followed_region, "sam_point", prompt, followed_frames
        return code_region, "code", None, {}

    if region is None:
        located = code_then_point()
        if located is None:
            _cleanup_sam_file()
            return _retake("light_not_found",
                           {"duration_s": norm_probe["duration_s"],
                            "stats": stats, "locator": "code",
                            "prompt_version": PROMPT_VERSION})
        region, locator, concept_used, sam_frames = located
    series = measure_region_series(frames, region)
    events = flashes_from_series(series, fps)
    if len(events) < 2 and locator == "sam":
        # A concept can land on the button beside the light. Try the
        # candidate that changes before giving up.
        located = code_then_point()
        if located is not None:
            region, locator, concept_used, sam_frames = located
            series = measure_region_series(frames, region)
            events = flashes_from_series(series, fps)
    # The SAM calls have ended. An uploaded file is deleted here, best
    # effort, so it does not sit on the files endpoint.
    _cleanup_sam_file()
    if len(events) < 2:
        return _retake("light_not_found",
                       {"duration_s": norm_probe["duration_s"],
                        "stats": stats, "locator": locator,
                        "concept": concept_used, "region": region,
                        "prompt_version": PROMPT_VERSION})
    bursts = group_bursts(events)
    decoded = decode_dell(bursts)
    seen, flag = (None, False)
    if live_client is not None:
        seen, flag = _describe_keyframes(frames, live_client,
                                         capture_id, intake_run_id)
    beeps = None
    try:
        from .beeps import beeps_from_file
        beeps = beeps_from_file(src)
    except Exception:
        beeps = None
    pattern = bursts_text(bursts)
    if decoded:
        shown = decoded["pattern"].replace(":", " ").replace(
            " white", ", white")
        summary = f"power light: {shown} - {decoded['meaning']}"
    else:
        summary = f"power light: {pattern} - no Dell match"
    if beeps and beeps.get("meaning"):
        summary += f"; beeps: {beeps['pattern_text']}"
    reading = {"locator": locator, "concept": concept_used,
               "region": region, "bursts": bursts, "events": events,
               "pattern": (decoded or {}).get("pattern"),
               "meaning": (decoded or {}).get("meaning"),
               "estimate": bool(decoded and decoded.get("estimate")),
               "sam_frames_seen": sorted(sam_frames)[:50],
               "duration_s": norm_probe["duration_s"],
               "fps": fps, "stats": stats, "seen": seen,
               "beeps": beeps, "summary": summary,
               "prompt_version": PROMPT_VERSION}
    return {"verdict": "ok", "retake": None, "reading": reading,
            "injection_flag": flag}

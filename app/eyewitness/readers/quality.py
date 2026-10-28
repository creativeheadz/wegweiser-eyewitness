"""On-server photo quality: blur, brightness, resolution (7.3).

A failure means `retake` with no model call. Thresholds are pure and
tested; `assess_image` measures a supplied photo with Pillow + numpy.
The phone page runs the same brightness rule before upload
(static/js/eyewitness/capture.js), so the two never disagree.
"""
from __future__ import annotations

import io

BLUR_MIN_VARIANCE = 60.0
BRIGHT_MIN = 25.0
BRIGHT_MAX = 245.0
# The black Windows 11 crash screen is dark on average with small bright
# text. A dark frame counts as too dark only when its brightest 0.1
# percent is dim as well: the crash screen reaches the reader, a photo
# taken in a pocket stops here.
HIGHLIGHT_MIN = 50.0
MIN_LONG_EDGE = 800

# The gate serves every reader, so its advice names no subject.
ADVICE = {
    "too_small": "This photo is too small to read. Take a new one with "
                 "the phone camera and try again.",
    "too_dark": "It is too dark to see anything. Turn on a light, then "
                "try again.",
    "glare": "The photo is too bright. Turn off the flash or tilt the "
             "phone slightly, then try again.",
    "blur": "The photo is out of focus. Hold the phone 30 cm away and "
            "tap the screen to focus.",
}


def _retake(reason: str) -> dict:
    return {"verdict": "retake",
            "retake": {"reason": reason, "advice": ADVICE[reason]}}


def verdict_for(*, blur_variance: float, brightness: float,
                long_edge: int, highlight: float | None = None) -> dict:
    """{verdict, retake} from measured numbers. Without `highlight`, a
    dark mean alone counts as too dark."""
    if long_edge < MIN_LONG_EDGE:
        return _retake("too_small")
    if brightness < BRIGHT_MIN and (highlight is None
                                    or highlight < HIGHLIGHT_MIN):
        return _retake("too_dark")
    if brightness > BRIGHT_MAX:
        return _retake("glare")
    if blur_variance < BLUR_MIN_VARIANCE:
        return _retake("blur")
    return {"verdict": "ok", "retake": None}


def measure_image(data: bytes) -> dict:
    """Laplacian variance, mean brightness and the brightest 0.1 percent
    on a 512px grey copy."""
    import numpy as np
    from PIL import Image
    with Image.open(io.BytesIO(data)) as img:
        long_edge = max(img.size)
        grey = img.convert("L")
        grey.thumbnail((512, 512))
        arr = np.asarray(grey, dtype=np.float64)
    brightness = float(arr.mean()) if arr.size else 0.0
    highlight = float(np.percentile(arr, 99.9)) if arr.size else 0.0
    # Laplacian variance via neighbour differences (no cv2 needed here).
    lap = (arr[:-2, 1:-1] + arr[2:, 1:-1] + arr[1:-1, :-2]
           + arr[1:-1, 2:] - 4 * arr[1:-1, 1:-1])
    return {"blur_variance": float(lap.var()) if lap.size else 0.0,
            "brightness": brightness, "highlight": highlight,
            "long_edge": int(long_edge)}


def assess_image(data: bytes) -> dict:
    stats = measure_image(data)
    return {**verdict_for(**stats), "quality": stats}


def read_photo(data: bytes) -> dict:
    """Reader contract for a photo no model reads (the whole device, the
    cables, a custom item): the quality gate only. The technician looks
    at the photo itself."""
    check = assess_image(data)
    if check["verdict"] == "retake":
        return {"verdict": "retake", "retake": check["retake"],
                "reading": {"quality": check["quality"]},
                "injection_flag": False}
    return {"verdict": "ok", "retake": None,
            "reading": {"quality": check["quality"],
                        "summary": "photo received"},
            "injection_flag": False}

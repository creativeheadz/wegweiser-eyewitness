"""On-server photo quality: blur, brightness, resolution (7.3).

A failure means `retake` with no model call. Thresholds are pure and
tested; `assess_image` measures a real photo with Pillow + numpy.
"""
from __future__ import annotations

import io

BLUR_MIN_VARIANCE = 60.0
BRIGHT_MIN = 25.0
BRIGHT_MAX = 245.0
MIN_LONG_EDGE = 800


def verdict_for(*, blur_variance: float, brightness: float,
                long_edge: int) -> dict:
    """{verdict, retake} from measured numbers."""
    if long_edge < MIN_LONG_EDGE:
        return {"verdict": "retake",
                "retake": {"reason": "too_small",
                           "advice": "Move closer so the screen fills the photo, then try again."}}
    if brightness < BRIGHT_MIN:
        return {"verdict": "retake",
                "retake": {"reason": "too_dark",
                           "advice": "It is too dark to read. Turn on a light or the phone torch and try again."}}
    if brightness > BRIGHT_MAX:
        return {"verdict": "retake",
                "retake": {"reason": "glare",
                           "advice": "There is glare on the screen. Tilt the phone slightly and try again."}}
    if blur_variance < BLUR_MIN_VARIANCE:
        return {"verdict": "retake",
                "retake": {"reason": "blur",
                           "advice": "The photo is out of focus. Hold the phone 30 cm away and tap the screen to focus."}}
    return {"verdict": "ok", "retake": None}


def measure_image(data: bytes) -> dict:
    """Laplacian variance + mean brightness on a 512px grey copy."""
    import numpy as np
    from PIL import Image
    with Image.open(io.BytesIO(data)) as img:
        long_edge = max(img.size)
        grey = img.convert("L")
        grey.thumbnail((512, 512))
        arr = np.asarray(grey, dtype=np.float64)
    brightness = float(arr.mean()) if arr.size else 0.0
    # Laplacian variance via neighbour differences (no cv2 needed here).
    lap = (arr[:-2, 1:-1] + arr[2:, 1:-1] + arr[1:-1, :-2]
           + arr[1:-1, 2:] - 4 * arr[1:-1, 1:-1])
    return {"blur_variance": float(lap.var()) if lap.size else 0.0,
            "brightness": brightness, "long_edge": int(long_edge)}


def assess_image(data: bytes) -> dict:
    stats = measure_image(data)
    return {**verdict_for(**stats), "quality": stats}

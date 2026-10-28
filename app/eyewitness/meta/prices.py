"""Meta Model API prices for Eyewitness (dev.meta.ai, checked 2026-10-26).

Single source for cost maths. `usd` values are dollars. Token prices are per
token; unit prices are per unit (image, video frame, audio hour, query).
"""
from __future__ import annotations

SPARK_INPUT_PER_TOKEN = 1.25 / 1_000_000
SPARK_CACHED_PER_TOKEN = 0.15 / 1_000_000
SPARK_OUTPUT_PER_TOKEN = 4.25 / 1_000_000
SAM_PER_IMAGE = 2.50 / 1_000
SAM_PER_FRAME = 0.20 / 1_000
SAM_MIN_FRAMES = 50
VOICE_PER_HOUR = 0.18
IMAGE_PER_IMAGE = 0.01
SEARCH_PER_QUERY = 2.50 / 1_000


def spark_usd(input_tokens=0, cached_tokens=0, output_tokens=0) -> float:
    cached = min(int(cached_tokens or 0), int(input_tokens or 0))
    fresh = max(int(input_tokens or 0) - cached, 0)
    return (fresh * SPARK_INPUT_PER_TOKEN + cached * SPARK_CACHED_PER_TOKEN
            + int(output_tokens or 0) * SPARK_OUTPUT_PER_TOKEN)


def sam_usd(images=0, frames=0) -> float:
    billed_frames = max(int(frames or 0), SAM_MIN_FRAMES) if frames else 0
    return int(images or 0) * SAM_PER_IMAGE + billed_frames * SAM_PER_FRAME


def voice_usd(seconds=0) -> float:
    return (float(seconds or 0) / 3600.0) * VOICE_PER_HOUR


def image_usd(count=1) -> float:
    return int(count or 0) * IMAGE_PER_IMAGE


def search_usd(queries=1) -> float:
    return int(queries or 0) * SEARCH_PER_QUERY


def usd_for(kind: str, *, input_tokens=0, cached_tokens=0, output_tokens=0,
            units=0) -> float:
    """Cost of one logged call. `units` means images for sam-image, frames
    for sam-video, seconds for voice, count for image/search."""
    if kind in ("intake", "spark", "search"):
        base = spark_usd(input_tokens, cached_tokens, output_tokens)
        return base + (search_usd(units) if kind == "search" else 0.0)
    if kind == "sam":
        # Caller passes frames in units when >0, else one image.
        return sam_usd(images=0, frames=units) if units else sam_usd(images=1)
    if kind == "voice":
        return voice_usd(units)
    if kind == "image":
        return image_usd(units or 1)
    return spark_usd(input_tokens, cached_tokens, output_tokens)

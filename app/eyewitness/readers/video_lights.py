"""Blinking-light decoding (7.3). The counting is code, not a model.

Pure helpers here: per-frame on/off with hysteresis inputs already
measured, grouping by gaps, and the Dell table lookup. SAM tracking +
ffmpeg normalisation join them in Phase 3.
"""
from __future__ import annotations

import json
import os
import statistics

_DELL = None


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
        if median and gap > 1.5 * median and nxt["colour"] == current["colour"]:
            bursts.append(current)
            current = {"colour": nxt["colour"], "count": 1}
        elif nxt["colour"] != current["colour"]:
            bursts.append(current)
            current = {"colour": nxt["colour"], "count": 1}
        else:
            current["count"] += 1
    bursts.append(current)
    # Merge adjacent same-colour bursts split only by a long gap is the
    # caller's decode input; keep bursts ordered as observed.
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

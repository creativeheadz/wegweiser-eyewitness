"""Voice note reader (7.3). Phase 1 stub: the transcribe + Spark
summary pipeline lands in Phase 3; the spike proves the endpoint."""
from __future__ import annotations

PROMPT_VERSION = "voice_summary_v1"


def read_voice(data: bytes, mime: str | None = None) -> dict:
    return {"verdict": "ok", "retake": None,
            "reading": {"stub": True, "prompt_version": PROMPT_VERSION,
                        "bytes": len(data or b"")},
            "injection_flag": False}

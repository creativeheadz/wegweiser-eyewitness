"""Muse Voice Transcribe client notes + guard (7.3, Phase 3 reader).

Realtime: wss://api.meta.ai/v1/asr/realtime, key in
`authorization.accessToken`, s16le mono 16/24 kHz paced near real
time. The file endpoint is per the Voice API reference. Phase 1 only
probes availability in the spike; no paid path is wired yet.
"""
from __future__ import annotations

REALTIME_URL = "wss://api.meta.ai/v1/asr/realtime"
MODEL = "muse-voice-transcribe-1.0"


def available() -> bool:
    from .client import configured
    return configured()

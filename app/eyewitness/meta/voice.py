"""Muse Voice Transcribe batch endpoint helpers (Phase 3).

The batch endpoint is POST /v1/asr/transcribe with multipart form
data: a JSON request part and a mono WAV audio part. The realtime
socket is not used. The response holds a transcript and timed
turns and no detected language field, so the Spark summary step
names the language and writes in English. The call itself, with
cache, cap and cost log, lives in meta/client.py as transcribe().
"""
from __future__ import annotations

BATCH_URL = "https://api.meta.ai/v1/asr/transcribe"
REALTIME_URL = "wss://api.meta.ai/v1/asr/realtime"
MODEL = "muse-voice-transcribe-1.0"


def available() -> bool:
    from .client import configured
    return configured()


def wav_stats(wav_bytes: bytes) -> dict:
    """Duration and level of one 16 kHz mono WAV, in code."""
    import io
    import wave
    import numpy as np
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        frames = wav.readframes(wav.getnframes())
        rate = wav.getframerate()
        width = wav.getsampwidth()
        count = wav.getnframes()
    if width == 2 and frames:
        samples = np.frombuffer(frames, dtype=np.int16).astype(
            np.float64) / 32768.0
    else:
        samples = np.zeros(0, dtype=np.float64)
    rms = float(np.sqrt(np.mean(samples ** 2))) if samples.size else 0.0
    return {"duration_s": (count / rate) if rate else 0.0,
            "sample_rate": rate, "rms": rms}

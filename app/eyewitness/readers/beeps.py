"""Beep codes from a video audio track (S3). No model.

An energy envelope finds tones, code counts them as long or short,
and a small table with its sources gives the pointer. A clip with no
audio track or no clear tones returns no beeps and changes nothing.
"""
from __future__ import annotations

import json
import os

_TABLE = None
LONG_MIN_S = 0.45
# The clip's audio is read for at most as long as the video reader
# keeps of the picture (video_lights.MAX_DURATION_S).
MAX_SECONDS = 20


def beep_table() -> dict:
    global _TABLE
    if _TABLE is None:
        path = os.path.join(os.path.dirname(__file__), "..", "decode",
                            "beep_codes.json")
        with open(path, encoding="utf-8") as fh:
            _TABLE = json.load(fh)
    return _TABLE


def decode_beeps(samples, sample_rate: int) -> dict:
    """Count beeps in mono samples. Returns the reading fragment.

    `samples` is a sequence of floats or ints at `sample_rate`. The
    envelope is the mean absolute value in 10 ms windows. A tone is a
    run over the adaptive mark that lasts 60 ms to 1.5 s.
    """
    import numpy as np
    empty = {"count": 0, "pattern": None, "pattern_text": "",
             "meaning": None, "source": None, "beeps": []}
    if samples is None or not sample_rate:
        return empty
    arr = np.asarray(samples, dtype=np.float64).ravel()
    if arr.size < int(sample_rate * 0.3):
        return empty
    if np.max(np.abs(arr)) > 2.0:
        arr = arr / 32768.0
    win = max(int(sample_rate * 0.01), 1)
    trimmed = arr[: (arr.size // win) * win]
    env = np.abs(trimmed).reshape(-1, win).mean(axis=1)
    if env.size < 30:
        return empty
    floor = float(np.percentile(env, 10))
    peak = float(np.percentile(env, 99))
    if peak - floor < 0.02 or peak < 0.03:
        return empty
    mark = floor + 0.45 * (peak - floor)
    tones: list[dict] = []
    start = None
    for i, value in enumerate(env):
        if start is None and value > mark:
            start = i
        elif start is not None and value <= mark:
            tones.append(_tone(start, i, win, sample_rate))
            start = None
    if start is not None:
        tones.append(_tone(start, len(env), win, sample_rate))
    tones = [t for t in tones if 0.06 <= t["duration_s"] <= 1.5]
    if not tones:
        return empty
    if len(tones) >= 6:
        gaps = [tones[i + 1]["start_s"] - tones[i]["end_s"]
                for i in range(len(tones) - 1)]
        if gaps and max(gaps) < 0.35:
            key = "continuous"
            return {"count": len(tones), "pattern": key,
                    "pattern_text": "continuous beeps",
                    "meaning": beep_table()["patterns"].get(key),
                    "source": "beep_codes.json", "beeps": tones}
    # Build the table key as counts per kind run, e.g. long:1 short:2.
    runs: list[str] = []
    for tone in tones:
        label = f"{tone['kind']}:1"
        if runs and runs[-1].split(":")[0] == tone["kind"]:
            kind, _, n = runs[-1].partition(":")
            runs[-1] = f"{kind}:{int(n) + 1}"
        else:
            runs.append(label)
    pattern_key = " ".join(runs)
    text = ", ".join(
        f"{r.split(':')[0]} x{r.split(':')[1]}" for r in runs)
    return {"count": len(tones), "pattern": pattern_key,
            "pattern_text": text,
            "meaning": beep_table()["patterns"].get(pattern_key),
            "source": "beep_codes.json", "beeps": tones}


def _tone(start_win: int, end_win: int, win: int,
          sample_rate: int) -> dict:
    start_s = start_win * win / sample_rate
    end_s = end_win * win / sample_rate
    duration = end_s - start_s
    return {"start_s": round(start_s, 3), "end_s": round(end_s, 3),
            "duration_s": round(duration, 3),
            "kind": "long" if duration >= LONG_MIN_S else "short"}


def beeps_from_wav(path: str) -> dict | None:
    """Beeps in one WAV file, or None when it holds no clear tones."""
    import wave
    import numpy as np
    try:
        with wave.open(path, "rb") as wav:
            frames = wav.readframes(wav.getnframes())
            rate = wav.getframerate()
            width = wav.getsampwidth()
            channels = wav.getnchannels()
    except Exception:
        return None
    if width == 2:
        samples = np.frombuffer(frames, dtype=np.int16).astype(
            np.float64) / 32768.0
    elif width == 4:
        samples = np.frombuffer(frames, dtype=np.int32).astype(
            np.float64) / 2147483648.0
    else:
        samples = np.frombuffer(frames, dtype=np.uint8).astype(
            np.float64) / 128.0 - 1.0
    if channels > 1:
        samples = samples[::channels]
    out = decode_beeps(samples, rate)
    return out if out.get("count") else None


def beeps_from_file(path: str) -> dict | None:
    """Beeps in a clip audio track, or None. ffmpeg extracts 16 kHz
    mono WAV with a timeout and no network; silence is not an error.
    """
    import subprocess
    import tempfile
    work = tempfile.mkdtemp(prefix="eyewitness-beeps-")
    wav_path = os.path.join(work, "audio.wav")
    try:
        done = subprocess.run(
            ["ffmpeg", "-y", "-nostdin", "-protocol_whitelist", "file",
             "-i", path, "-t", str(MAX_SECONDS), "-vn", "-ac", "1",
             "-ar", "16000", "-c:a", "pcm_s16le", wav_path],
            capture_output=True, timeout=60)
        if done.returncode != 0 or not os.path.exists(wav_path):
            return None
        return beeps_from_wav(wav_path)
    except Exception:
        return None
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)

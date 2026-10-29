"""Beep codes from a video audio track (S3) or a beep recording. No model.

An energy envelope finds tones, code counts them as long or short,
and a small table with its sources gives the pointer. A clip with no
audio track or no clear tones returns no beeps and changes nothing.
A beep recording (the beep_recording template) counts only tonal
segments, so speech and noise never count as beeps.
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


TONAL_MIN = 0.45
TONAL_LOW_HZ = 300.0
TONAL_HIGH_HZ = 4000.0
# The tonal path accepts a quieter recording than the clip path: the
# tonal check, not the level, keeps speech and noise out. A PC speaker
# filmed from 30 cm with the browser's gain control off reaches about
# 0.02 to 0.05 of full scale (29 Oct, synthetic levels over fan noise).
TONAL_MIN_RANGE = 0.004
TONAL_MIN_PEAK = 0.006
# Too quiet means the loudest moment stays under the voice bar. A few
# short beeps leave a recording mostly silent, so its mean level says
# little. The phone page reads the RMS of 2048 samples (about 43 ms)
# every quarter second and holds the loudest reading to the same bar.
LEVEL_WINDOW_S = 0.043
# A vowel can be as pure as a beep for a tenth of a second, so the
# recording path asks more of a tone than purity. A beep lasts at
# least 0.15 s, holds its pitch to within 3 percent and its level
# steady, and every beep in a pattern is the same tone. On 29 Oct the
# voice notes and the film's voice-over gave vowel segments of 0.06
# to 0.24 s that were pure enough, and none that also passed these.
STEADY_MIN_S = 0.15
PITCH_SPREAD_MAX = 0.03
LEVEL_CV_MAX = 0.2
SAME_TONE = 0.04


def _tonal_ratio(segment, sample_rate: int) -> float:
    """Share of energy in a narrow band around the peak, 300 Hz to 4 kHz.

    A beep puts most of its energy in one narrow band. Speech spreads
    energy across harmonics and noise spreads it across the spectrum,
    so both score low. Measured on synthetic tones, speech-like notes,
    noise and silence; the bar sits with a margin on both sides.
    """
    import numpy as np
    seg = np.asarray(segment, dtype=np.float64).ravel()
    if seg.size < 32:
        return 0.0
    seg = seg - float(seg.mean())
    spectrum = np.abs(np.fft.rfft(seg * np.hanning(seg.size))) ** 2
    freqs = np.fft.rfftfreq(seg.size, d=1.0 / sample_rate)
    band = (freqs >= TONAL_LOW_HZ) & (freqs <= TONAL_HIGH_HZ)
    if not band.any() or spectrum.sum() <= 0:
        return 0.0
    masked = np.where(band, spectrum, 0.0)
    peak = int(np.argmax(masked))
    half = max(2, int(round(0.06 * peak)))
    lo = max(0, peak - half)
    hi = min(spectrum.size, peak + half + 1)
    return float(spectrum[lo:hi].sum() / spectrum.sum())


def _peak_track(segment, sample_rate: int) -> list[float]:
    """The strongest frequency between 300 Hz and 4 kHz in each 20 ms."""
    import numpy as np
    seg = np.asarray(segment, dtype=np.float64).ravel()
    frame = max(int(sample_rate * 0.02), 32)
    size = 4096
    while size < frame:
        size *= 2
    freqs = np.fft.rfftfreq(size, d=1.0 / sample_rate)
    band = (freqs >= TONAL_LOW_HZ) & (freqs <= TONAL_HIGH_HZ)
    if not band.any():
        return []
    window = np.hanning(frame)
    out = []
    for start in range(0, seg.size - frame + 1, frame):
        part = seg[start:start + frame]
        spectrum = np.abs(np.fft.rfft((part - part.mean()) * window, n=size)) ** 2
        out.append(float(freqs[band][int(np.argmax(spectrum[band]))]))
    return out


def _steady_tone(segment, levels, sample_rate: int) -> float | None:
    """The pitch of one segment when it is a steady tone, else None.

    Steady means pure (the tonal ratio), one pitch from start to end
    and one level, which is what a beep is and a vowel is not.
    """
    import numpy as np
    if _tonal_ratio(segment, sample_rate) < TONAL_MIN:
        return None
    peaks = _peak_track(segment, sample_rate)
    if len(peaks) < 2:
        return None
    low, mid, high = np.percentile(peaks, [10, 50, 90])
    if mid <= 0 or (high - low) / mid > PITCH_SPREAD_MAX:
        return None
    inner = np.asarray(levels, dtype=np.float64)
    inner = inner[1:-1] if inner.size > 4 else inner
    if inner.size and float(np.std(inner)) > LEVEL_CV_MAX * max(float(np.mean(inner)), 1e-9):
        return None
    return float(mid)


def decode_beeps(samples, sample_rate: int, *, require_tonal: bool = False) -> dict:
    """Count beeps in mono samples. Returns the reading fragment.

    `samples` is a sequence of floats or ints at `sample_rate`. The
    envelope is the mean absolute value in 10 ms windows. A tone is a
    run over the adaptive mark that lasts 60 ms to 1.5 s. With
    `require_tonal` (a beep recording) a tone must also be steady,
    last 0.15 s or more and share its pitch with the other beeps, so
    speech and noise never count as beeps.
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
    if require_tonal:
        # One short beep in twenty seconds fills one percent of the
        # windows, so the peak comes from higher up the envelope.
        peak = float(np.percentile(env, 99.5))
        min_range, min_peak = TONAL_MIN_RANGE, TONAL_MIN_PEAK
    else:
        peak = float(np.percentile(env, 99))
        min_range, min_peak = 0.02, 0.03
    if peak - floor < min_range or peak < min_peak:
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
    held = [t for t in tones if t["duration_s"] > 1.5]
    tones = [t for t in tones if 0.06 <= t["duration_s"] <= 1.5]
    if require_tonal:
        def pitch(tone: dict) -> float | None:
            first = int(tone["start_s"] * sample_rate)
            last = int(tone["end_s"] * sample_rate)
            levels = env[int(round(tone["start_s"] * sample_rate / win)):
                         int(round(tone["end_s"] * sample_rate / win))]
            hz = _steady_tone(arr[first:last], levels, sample_rate)
            if hz is not None:
                tone["hz"] = round(hz)
            return hz
        steady = [t for t in tones
                  if t["duration_s"] >= STEADY_MIN_S and pitch(t) is not None]
        if steady:
            # The beeps of one pattern share a tone. The pitch most
            # segments sit near wins, and a stray match elsewhere drops.
            def near(a: dict, b: dict) -> bool:
                return abs(a["hz"] - b["hz"]) <= SAME_TONE * b["hz"]
            ref = max(steady, key=lambda t: (
                sum(near(o, t) for o in steady),
                sum(o["duration_s"] for o in steady if near(o, t))))
            steady = [t for t in steady if near(t, ref)]
        tones = steady
        # A steady tone longer than any beep is the continuous pattern.
        held = [t for t in held if pitch(t) is not None]
        if held:
            key = "continuous"
            every = sorted(held + tones, key=lambda t: t["start_s"])
            return {"count": len(every), "pattern": key,
                    "pattern_text": "continuous tone",
                    "meaning": beep_table()["patterns"].get(key),
                    "source": "beep_codes.json", "beeps": every}
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


BEEP_RETAKE_DEFAULTS = {
    "too_short": "The recording is too short. Record until the beeps stop.",
    "too_quiet": "The recording is too quiet. Hold the phone closer to the computer and try again.",
    "no_beeps": "No beeps could be heard. Hold the phone close to the computer and record again while it starts.",
    "unreadable": "The recording could not be read. Please record it again.",
}


def _wav_samples(path: str):
    import wave
    import numpy as np
    with wave.open(path, "rb") as wav:
        frames = wav.readframes(wav.getnframes())
        rate = wav.getframerate()
        width = wav.getsampwidth()
        channels = wav.getnchannels()
    if width == 2:
        samples = np.frombuffer(frames, dtype=np.int16).astype(np.float64) / 32768.0
    elif width == 4:
        samples = np.frombuffer(frames, dtype=np.int32).astype(np.float64) / 2147483648.0
    else:
        samples = np.frombuffer(frames, dtype=np.uint8).astype(np.float64) / 128.0 - 1.0
    if channels > 1:
        samples = samples[::channels]
    return samples, rate


def loudest_level(samples, sample_rate: int) -> float:
    """RMS of the loudest 43 ms of a recording, windows half overlapped."""
    import numpy as np
    arr = np.asarray(samples, dtype=np.float64).ravel()
    if not arr.size or not sample_rate:
        return 0.0
    win = max(int(sample_rate * LEVEL_WINDOW_S), 1)
    if arr.size <= win:
        return float(np.sqrt(np.mean(arr ** 2)))
    loudest = 0.0
    for offset in (0, win // 2):
        part = arr[offset:]
        count = part.size // win
        if not count:
            continue
        blocks = part[: count * win].reshape(count, win)
        loudest = max(loudest, float(np.sqrt((blocks ** 2).mean(axis=1)).max()))
    return loudest


def read_beeps(data: bytes, mime: str | None = None, spec: dict | None = None) -> dict:
    """Reader contract for one beep recording. Code only, no model."""
    import shutil
    import subprocess
    import tempfile
    spec = spec or {}
    retake_map = dict(BEEP_RETAKE_DEFAULTS)
    retake_map.update(spec.get("retake") or {})

    def retake(reason, reading=None):
        return {"verdict": "retake",
                "retake": {"reason": reason, "advice": retake_map[reason]},
                "reading": reading or {"template": spec.get("id"),
                                        "catalogue_version": spec.get("version")},
                "injection_flag": False}

    if not data:
        return retake("unreadable")
    work = tempfile.mkdtemp(prefix="eyewitness-beep-rec-")
    try:
        src = os.path.join(work, "source.bin")
        dst = os.path.join(work, "beeps.wav")
        with open(src, "wb") as fh:
            fh.write(data)
        done = subprocess.run(
            ["ffmpeg", "-y", "-nostdin", "-protocol_whitelist", "file",
             "-i", src, "-t", str(MAX_SECONDS), "-vn", "-ac", "1",
             "-ar", "16000", "-c:a", "pcm_s16le", dst],
            capture_output=True, timeout=60)
        if done.returncode != 0 or not os.path.exists(dst):
            return retake("unreadable")
        try:
            samples, rate = _wav_samples(dst)
        except Exception:
            return retake("unreadable")
        import numpy as np
        duration = float(samples.size / rate) if rate else 0.0
        rms = float(np.sqrt(np.mean(samples ** 2))) if samples.size else 0.0
        loudest = loudest_level(samples, rate)
        from .voice import MIN_RMS, MIN_SECONDS
        reading = {"template": spec.get("id"),
                   "catalogue_version": spec.get("version"),
                   "stats": {"duration_s": duration, "rms": rms,
                             "loudest_rms": loudest}}
        if duration < MIN_SECONDS:
            return retake("too_short", reading)
        if loudest < MIN_RMS:
            return retake("too_quiet", reading)
        found = decode_beeps(samples, rate, require_tonal=True)
        if not found.get("count"):
            return retake("no_beeps", reading)
        reading.update({"count": found["count"], "pattern": found["pattern"],
                        "pattern_text": found["pattern_text"],
                        "meaning": found["meaning"], "source": found["source"],
                        "beeps": found["beeps"]})
        summary = f"beeps: {found['pattern_text']}"
        if found.get("meaning"):
            summary += "; " + str(found["meaning"])[:160]
        reading["summary"] = summary[:300]
        return {"verdict": "ok", "retake": None, "reading": reading,
                "injection_flag": False}
    except Exception:
        return retake("unreadable")
    finally:
        shutil.rmtree(work, ignore_errors=True)


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

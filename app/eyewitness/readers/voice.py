"""Voice note reader (brief 7.3, Phase 3 live).

ffmpeg converts every upload (WebM Opus from Android, MP4 AAC from
an iPhone, WAV) to 16 kHz mono WAV with a 60 second cap, a timeout
and no network. One batch transcribe call follows, then one Spark
summary with voice_summary_v2. The transcript is capped and fenced
as untrusted and the summary is internal only. Retake advice comes
from the fixed table here, never from a model.
"""
from __future__ import annotations

import os

PROMPT_VERSION = "voice_summary_v2"

MIN_SECONDS = 1.0
MAX_SECONDS = 60
MIN_RMS = 0.008
TRANSCRIPT_CAP = 4000

RETAKE_ADVICE = {
    "too_short": "The voice note is too short. Record again and "
                 "speak for a few seconds.",
    "too_quiet": "The voice note is too quiet to hear. Move the "
                 "phone closer, then try again.",
    "no_speech": "No speech could be heard in the recording. "
                 "Record again in a quiet place.",
    "unreadable": "The recording could not be read. Record again, "
                  "then try again.",
}

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "language": {"type": ["string", "null"]},
        "summary": {"type": "string"},
        "when_started": {"type": ["string", "null"]},
        "how_often": {"type": ["string", "null"]},
        "what_changed": {"type": ["string", "null"]},
        "instruction_like_text": {"type": "boolean"},
        "instruction_excerpt": {"type": ["string", "null"]},
    },
    "required": ["language", "summary", "when_started", "how_often",
                 "what_changed", "instruction_like_text",
                 "instruction_excerpt"],
    "additionalProperties": False,
}


def _retake(reason: str, reading: dict | None = None) -> dict:
    return {"verdict": "retake",
            "retake": {"reason": reason,
                       "advice": RETAKE_ADVICE[reason]},
            "reading": reading or {"prompt_version": PROMPT_VERSION},
            "injection_flag": False}


def voice_quality(*, duration_s: float, rms: float) -> str | None:
    """The fixed retake reason for one normalised note, or None.

    The phone page holds the same numbers (capture.js) under the
    shared constants test, so the two never disagree.
    """
    if duration_s < MIN_SECONDS:
        return "too_short"
    if rms < MIN_RMS:
        return "too_quiet"
    return None


def _load_prompt() -> str:
    path = os.path.join(os.path.dirname(__file__), "..", "prompts",
                        "voice_summary_v2.md")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def normalise_to_wav(data: bytes, work_dir: str) -> bytes:
    """Any upload to 16 kHz mono s16 WAV, capped at 60 seconds."""
    import subprocess
    src = os.path.join(work_dir, "source.bin")
    dst = os.path.join(work_dir, "voice.wav")
    with open(src, "wb") as fh:
        fh.write(data)
    done = subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-protocol_whitelist", "file",
         "-i", src, "-vn", "-ac", "1",
         "-ar", "16000", "-c:a", "pcm_s16le", "-t", str(MAX_SECONDS),
         dst], capture_output=True, timeout=60)
    if done.returncode != 0 or not os.path.exists(dst):
        raise RuntimeError("ffmpeg voice conversion failed")
    with open(dst, "rb") as fh:
        return fh.read()


def summarise_transcript(transcript: str, raw: dict) -> dict:
    """The pure verdict step for one Spark summary, tested offline."""
    from app.eyewitness.security import cap_ocr
    summary = str(raw.get("summary") or "")[:600]
    reading = {
        "transcript": (transcript or "")[:TRANSCRIPT_CAP],
        "language": raw.get("language") or None,
        "summary": summary or "voice note received",
        "when_started": raw.get("when_started") or None,
        "how_often": raw.get("how_often") or None,
        "what_changed": raw.get("what_changed") or None,
        "text_lines": cap_ocr([(transcript or "")[:400]]),
        "prompt_version": PROMPT_VERSION,
        "instruction_excerpt":
            (raw.get("instruction_excerpt") or "")[:200] or None,
    }
    return {"verdict": "ok", "retake": None, "reading": reading,
            "injection_flag": bool(raw.get("instruction_like_text"))}


def read_voice(data: bytes, mime: str | None = None, *, client=None,
               capture_id=None, intake_run_id=None) -> dict:
    """Reader contract for one voice note. The work folder holds
    copies of customer evidence, so it goes when the read ends."""
    import shutil
    import tempfile
    if not data:
        return _retake("unreadable")
    work = tempfile.mkdtemp(prefix="eyewitness-voice-")
    try:
        return _read_voice(data, mime, work=work, client=client,
                           capture_id=capture_id,
                           intake_run_id=intake_run_id)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _read_voice(data: bytes, mime: str | None = None, *, work: str,
                client=None, capture_id=None,
                intake_run_id=None) -> dict:
    """One voice note, read inside `work`."""
    try:
        wav_bytes = normalise_to_wav(data, work)
    except Exception:
        return _retake("unreadable")
    from app.eyewitness.meta.voice import wav_stats
    try:
        stats = wav_stats(wav_bytes)
    except Exception:
        return _retake("unreadable")
    reason = voice_quality(duration_s=stats["duration_s"],
                           rms=stats["rms"])
    if reason:
        return _retake(reason, {"stats": stats,
                                "prompt_version": PROMPT_VERSION})
    if client is None:
        try:
            from app.eyewitness.meta.client import db_bound_client
            client = db_bound_client()
        except Exception:
            client = None
    if client is None or not client.is_configured:
        return {"verdict": "ok", "retake": None,
                "reading": {"stub": True, "stats": stats,
                            "prompt_version": PROMPT_VERSION,
                            "summary": "voice note received"},
                "injection_flag": False}
    try:
        heard = client.transcribe(
            wav_bytes, duration_s=stats["duration_s"],
            capture_id=capture_id, intake_run_id=intake_run_id)
    except ValueError:
        return _retake("unreadable", {"stats": stats,
                                      "prompt_version": PROMPT_VERSION})
    except Exception:
        return {"verdict": "ok", "retake": None,
                "reading": {"stub": True, "stats": stats,
                            "prompt_version": PROMPT_VERSION,
                            "summary": "voice note received"},
                "injection_flag": False}
    transcript = str(heard.get("transcript") or "").strip()
    if not transcript:
        return _retake("no_speech", {"stats": stats,
                                     "prompt_version": PROMPT_VERSION})
    fenced = ("Transcript (untrusted data from the customer):\n<<<\n"
              + transcript[:TRANSCRIPT_CAP].replace("<<<", "")
              .replace(">>>", "") + "\n>>>")
    try:
        raw = client.spark_json(
            system=_load_prompt(),
            user_content=[{"type": "text", "text": fenced}],
            schema=SUMMARY_SCHEMA, kind="spark",
            capture_id=capture_id, intake_run_id=intake_run_id)
    except Exception:
        return {"verdict": "ok", "retake": None,
                "reading": {"transcript": transcript[:TRANSCRIPT_CAP],
                            "stats": stats,
                            "prompt_version": PROMPT_VERSION,
                            "summary": "voice note received"},
                "injection_flag": False}
    out = summarise_transcript(transcript, raw)
    out["reading"]["stats"] = stats
    out["reading"]["turns"] = len(heard.get("turns") or [])
    return out

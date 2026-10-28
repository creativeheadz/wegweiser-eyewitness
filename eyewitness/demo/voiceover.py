#!/usr/bin/env python3
"""Voice-over for the demo cut, rendered with ElevenLabs.

The narrator reads each scene's caption from storyboard.yaml. Scene 2 is a
phone call, so it is read as one: the narrator, the technician and Brenda.
Every file is transcribed with Scribe and kept only when it says its script
in order (85 percent word coverage). A miss is rendered again with a new
seed, once.

--voice-note renders Brenda's German voice note for scene 8. That file then
goes through the Eyewitness voice reader like any customer's, so the scene
8 card shows what Muse Voice Transcribe and Muse Spark made of it.

    ELEVENLABS_API_KEY=... EYEWITNESS_DEMO_DIR=<demo folder> \\
        python eyewitness/demo/voiceover.py [--scene N ...] [--voice-note]

The key travels by environment only and is never printed. Needs PyYAML and
ffmpeg on PATH.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.elevenlabs.io"
MODEL = "eleven_v4"
STT_MODEL = "scribe_v2"
COVERAGE = 0.85
HERE = Path(__file__).resolve().parent

VOICES = {
    "NARRATOR": "pFZP5JQG7iQjIQuC4Bku",  # Lily, the Old Forge narrator
    "TECH": "JBFqnCBsd6RMkjVDRZzb",      # George, the IT technician
    "BRENDA": "fBs1tCpaSMsPcbMkLQlk",    # Yvonne, a native German voice
}

# Scene 2 is the phone call every helpdesk knows.
LINES = {
    2: [("NARRATOR", "Every IT helpdesk knows this call."),
        ("TECH", "What does the screen say?"),
        ("BRENDA", "It went black. There's some tiny writing at the "
                   "bottom.")],
}

VOICE_NOTE_DE = (
    "Hallo, hier ist Brenda vom Empfang. Mein Computer ist heute Morgen "
    "zweimal abgestürzt. Der Bildschirm wurde schwarz, und ganz unten stand "
    "eine kleine Zeile mit einem Fehlercode. Jetzt startet er gar nicht "
    "mehr, und das Licht am Einschaltknopf blinkt orange und weiß. Wir "
    "haben nichts verändert. Können Sie sich das bitte ansehen?")


def _post(path: str, key: str, body: bytes, content_type: str,
          timeout: int = 180) -> bytes:
    req = urllib.request.Request(
        API + path, data=body, method="POST",
        headers={"xi-api-key": key, "Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:300]
        raise SystemExit(f"ElevenLabs {path} HTTP {exc.code}: {detail}")


def dialogue(key: str, inputs, language: str, seed: int) -> bytes:
    """One text-to-dialogue render as MP3 bytes."""
    body = {"inputs": [{"text": text, "voice_id": VOICES[who]}
                       for who, text in inputs],
            "model_id": MODEL, "seed": seed, "language_code": language}
    return _post("/v1/text-to-dialogue?output_format=mp3_44100_128", key,
                 json.dumps(body).encode("utf-8"), "application/json")


def scribe(key: str, audio: bytes, language: str) -> str:
    boundary = "----eyewitnessvo"
    parts = []
    for name, value in (("model_id", STT_MODEL), ("language_code", language)):
        parts += [f"--{boundary}".encode(),
                  f'Content-Disposition: form-data; name="{name}"'.encode(),
                  b"", value.encode()]
    parts += [f"--{boundary}".encode(),
              b'Content-Disposition: form-data; name="file"; '
              b'filename="take.mp3"',
              b"Content-Type: audio/mpeg", b"", audio,
              f"--{boundary}--".encode(), b""]
    raw = _post("/v1/speech-to-text", key, b"\r\n".join(parts),
                f"multipart/form-data; boundary={boundary}", timeout=300)
    return json.loads(raw).get("text", "")


# Numbers come back as digits or as words ("8:57", "eight fifty-seven"),
# so both sides drop them before matching, as the podcast check does.
NUMBER_WORDS = set("""zero one two three four five six seven eight nine ten
eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen
twenty thirty forty fifty sixty seventy eighty ninety hundred thousand point
percent""".split())


def _words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return [w for w in re.findall(r"[a-z0-9]+", text)
            if not w.isdigit() and w not in NUMBER_WORDS]


def coverage(script: str, heard: str) -> float:
    """Share of the script's words heard in order."""
    want, got = _words(script), _words(heard)
    if not want:
        return 1.0
    matcher = difflib.SequenceMatcher(a=want, b=got, autojunk=False)
    return sum(b.size for b in matcher.get_matching_blocks()) / len(want)


def _to_wav(mp3: Path, wav: Path) -> float:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp3),
                    "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le",
                    str(wav)], check=True, timeout=60)
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration", "-of", "csv=p=0", str(wav)],
                         capture_output=True, text=True, timeout=30)
    return float(out.stdout.strip() or 0)


def render(key: str, inputs, language: str, out_wav: Path,
           label: str) -> None:
    """Render, check by transcription, keep. Two seeds at most."""
    script = " ".join(text for _, text in inputs)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    mp3 = out_wav.with_suffix(".mp3")
    best = 0.0
    for seed in (11, 23):
        audio = dialogue(key, inputs, language, seed)
        mp3.write_bytes(audio)
        heard = scribe(key, audio, language)
        best = coverage(script, heard)
        if best >= COVERAGE:
            seconds = _to_wav(mp3, out_wav)
            print(f"{label}: kept seed {seed}, {seconds:.1f} s, "
                  f"coverage {best:.0%}")
            return
        print(f"{label}: seed {seed} coverage {best:.0%}, heard "
              f"{heard[:160]!r}; rendering again")
    raise SystemExit(f"{label}: no take reached {COVERAGE:.0%} "
                     f"(best {best:.0%})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", type=int, action="append", default=[],
                        help="render only these scenes (repeatable)")
    parser.add_argument("--voice-note", action="store_true",
                        help="render Brenda's German voice note for scene 8")
    args = parser.parse_args()
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    demo_dir = os.environ.get("EYEWITNESS_DEMO_DIR", "").strip()
    if not key or not demo_dir:
        raise SystemExit("set ELEVENLABS_API_KEY and EYEWITNESS_DEMO_DIR")
    voice_dir = Path(demo_dir) / "voice"
    import yaml
    scenes = yaml.safe_load(
        (HERE / "storyboard.yaml").read_text(encoding="utf-8"))["scenes"]
    for scene in scenes:
        number = int(scene["number"])
        if args.scene and number not in args.scene:
            continue
        inputs = LINES.get(number) or [("NARRATOR", scene["caption"])]
        render(key, inputs, "en", voice_dir / f"scene{number:02d}.wav",
               f"scene {number}")
    if args.voice_note:
        render(key, [("BRENDA", VOICE_NOTE_DE)], "de",
               voice_dir / "brenda-voicenote-de.wav", "voice note")
    return 0


if __name__ == "__main__":
    sys.exit(main())

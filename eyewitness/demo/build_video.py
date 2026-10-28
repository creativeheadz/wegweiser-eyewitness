#!/usr/bin/env python3
"""Build the Eyewitness demo video with ffmpeg.

Reads storyboard.yaml beside this file. Each scene becomes one
1920 by 1080 segment at 30 frames per second with its voice, a
burned caption and a corner badge. Segments join with crossfades.
Sound is trimmed for silences and set with loudnorm. The build
fails when the total runs past 2:58. Title wording sits over the
first scene and the end card over the last one carries the GitHub
address. With --vertical a second cut at 1080 by 1920 is made from
scenes 1, 2, 5, 7, 9 and 11.

Captions come from the voice-over files through Voice Transcribe
when those files sit in the demo folder. At other times the
caption is the storyboard wording for the scene. A voice file that
Andrei has not recorded yet is spoken for the draft by ffmpeg
flite, and its file name marks it as a placeholder. Phone scenes
keep the caption beside or below the phone screen. The build
prints a WARNING when a caption names a language that the voice
reading for that scene does not have.

Usage:
  EYEWITNESS_DEMO_DIR=/path/to/demo-media venv/bin/python \
    eyewitness/demo/build_video.py [--vertical] [--out path]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

GITHUB_URL = "https://github.com/creativeheadz/wegweiser-eyewitness"
MAX_TOTAL_S = 178  # 2:58
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _run(cmd: list[str], timeout: int = 300) -> None:
    done = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout)
    if done.returncode != 0:
        tail = (done.stderr or "")[-1200:]
        raise RuntimeError(f"command failed: {cmd[0]} {tail}")


def _probe_duration(path: Path) -> float:
    done = subprocess.run(
        ["ffprobe", "-v", "error", "-protocol_whitelist", "file",
         "-show_entries", "format=duration", "-of",
         "csv=p=0", str(path)], capture_output=True, text=True,
        timeout=30)
    try:
        return float((done.stdout or "0").strip() or 0)
    except ValueError:
        return 0.0


def _flite_voice(text: str, target: Path) -> Path:
    """One placeholder line spoken by flite. Draft only."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 0:
        return target
    words = target.with_suffix(".txt")
    words.write_text(text, encoding="utf-8")
    _run(["ffmpeg", "-y", "-nostdin", "-protocol_whitelist", "file",
          "-f", "lavfi", "-i", f"flite=textfile={words}:voice=slt",
          "-ar", "24000", "-ac", "1", str(target)], timeout=60)
    return target


def _placeholder_card(scene: dict, target: Path, size=(1920, 1080)) -> Path:
    from PIL import Image, ImageDraw, ImageFont
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return target
    img = Image.new("RGB", size, (32, 34, 38))
    draw = ImageDraw.Draw(img)
    try:
        big = ImageFont.truetype(FONT, 54)
        mid = ImageFont.truetype(FONT_REG, 32)
    except Exception:
        big = ImageFont.load_default()
        mid = ImageFont.load_default()
    draw.text((80, 80), f"Scene {scene['number']}", fill=(255, 190, 90),
              font=big)
    label = str(scene.get("visual") or "")
    y = 180
    for line in textwrap.wrap(label, 52)[:4]:
        draw.text((80, y), line, fill=(235, 235, 235), font=mid)
        y += 44
    note = "Desktop recording to follow. Sign-in is needed for this scene."
    for line in textwrap.wrap(note, 52)[:3]:
        draw.text((80, y + 30), line, fill=(170, 170, 170), font=mid)
        y += 44
    img.save(target, format="PNG")
    return target


def _caption_text(scene: dict, demo_dir: Path) -> str:
    """Caption wording for one scene.

    A recorded voice file goes through Voice Transcribe and its
    transcript becomes the caption. Without that file the
    storyboard wording is the caption.
    """
    voice_rel = scene.get("voiceover") or ""
    voice = demo_dir / voice_rel if voice_rel else None
    if voice is not None and voice.exists() and voice.stat().st_size > 0:
        try:
            from app.eyewitness.meta.client import MetaClient
            client = MetaClient()
            if client.is_configured:
                wav = voice
                if voice.suffix.lower() != ".wav":
                    import tempfile
                    tmp = Path(tempfile.mktemp(suffix=".wav"))
                    _run(["ffmpeg", "-y", "-nostdin",
                          "-protocol_whitelist", "file", "-i", str(voice),
                          "-ac", "1", "-ar", "16000", str(tmp)], timeout=60)
                    wav = tmp
                heard = client.transcribe(wav.read_bytes(),
                                          duration_s=_probe_duration(wav))
                text = str(heard.get("transcript") or "").strip()
                if text:
                    return text
        except Exception:
            pass
    return str(scene.get("caption") or "")


_LANGUAGE_NAMES = {
    "en": "English", "eng": "English", "english": "English",
    "de": "German", "deu": "German", "ger": "German",
    "german": "German", "deutsch": "German",
    "fr": "French", "fra": "French", "french": "French",
    "es": "Spanish", "spa": "Spanish", "spanish": "Spanish",
    "it": "Italian", "ita": "Italian", "italian": "Italian",
    "nl": "Dutch", "nld": "Dutch", "dutch": "Dutch",
    "pt": "Portuguese", "por": "Portuguese", "portuguese": "Portuguese",
    "pl": "Polish", "pol": "Polish", "polish": "Polish",
}


def _language_name(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    key = text.lower().split("-")[0].split("_")[0]
    if key in _LANGUAGE_NAMES:
        return _LANGUAGE_NAMES[key]
    # A full name from a reading keeps its spelling, capitalised.
    return text[:1].upper() + text[1:]


def caption_language_warnings(scenes, readings) -> list[str]:
    """One warning per scene whose caption names a note language
    that the voice reading for that scene does not have."""
    import re
    warnings: list[str] = []
    for scene in scenes or []:
        caption = str(scene.get("caption") or "")
        found = re.search(
            r"(?:recorded|spoken|heard)\s+in\s+([A-Za-z]+)", caption,
            flags=re.IGNORECASE)
        if not found:
            continue
        claimed = _language_name(found.group(1))
        number = int(scene.get("number") or 0)
        actual_raw = (readings or {}).get(number)
        if actual_raw is None:
            actual_raw = (readings or {}).get(str(number))
        actual = _language_name(actual_raw)
        if not actual:
            warnings.append(
                f"WARNING: scene {number} caption says the voice note "
                f"was recorded in {claimed}, but no voice reading was "
                "found for that scene.")
        elif actual.lower() != claimed.lower():
            warnings.append(
                f"WARNING: scene {number} caption says the voice note "
                f"was recorded in {claimed}, but the voice reading is "
                f"in {actual}.")
    return warnings


def _voice_readings(demo_dir: Path, scenes) -> dict:
    """Voice reading languages saved beside the overlay cards."""
    import json
    readings: dict = {}
    for scene in scenes or []:
        number = int(scene.get("number") or 0)
        sidecar = demo_dir / "overlays" / f"scene{number:02d}-voice.json"
        if not sidecar.exists():
            continue
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        language = data.get("language") if isinstance(data, dict) else None
        if language:
            readings[number] = language
    return readings


def _badge_filter(badge, width: int) -> str:
    if not badge:
        return ""
    text = str(badge).replace("'", " ")
    colour = "orange" if text == "NEW" else "gray"
    return (f",drawtext=fontfile={FONT}:text='{text}':fontsize=30:"
            f"fontcolor=white:box=1:boxcolor={colour}@0.85:"
            f"boxborderw=12:x=w-tw-36:y=28")


def _segment(scene: dict, demo_dir: Path, work: Path, *,
             vertical: bool = False) -> Path:
    """One scene as a finished segment with voice and caption."""
    import yaml  # noqa: F401  (storyboard already loaded by caller)
    number = int(scene["number"])
    duration = float(scene["duration"])
    width, height = (1080, 1920) if vertical else (1920, 1080)
    visual_rel = str(scene.get("visual") or "")
    visual = demo_dir / visual_rel
    if not visual.exists():
        card = work / f"card-{number:02d}.png"
        visual = _placeholder_card(scene, card, size=(width, height))
    voice_rel = str(scene.get("voiceover") or "")
    voice = demo_dir / voice_rel if voice_rel else None
    is_placeholder_voice = False
    if voice is None or not voice.exists():
        voice = demo_dir / "voice-placeholder" / f"scene{number:02d}-placeholder-flite.wav"
        _flite_voice(str(scene.get("caption") or ""), voice)
        is_placeholder_voice = True
    caption = _caption_text(scene, demo_dir) if not is_placeholder_voice \
        else str(scene.get("caption") or "")
    is_phone = visual_rel.startswith("phone/")
    # Wrap the caption for the burned line. Phone recordings keep
    # the caption beside or below the phone screen, never on it.
    # The wording goes through a text file, so punctuation stays whole.
    if is_phone and not vertical:
        wrap_width = 40
    elif is_phone:
        wrap_width = 42
    else:
        wrap_width = 58
    tag = "v" if vertical else "h"
    cap_file = work / f"cap-{number:02d}-{tag}.txt"
    cap_file.write_text(
        "\n".join(textwrap.wrap(caption, wrap_width)[:6 if is_phone else 3]),
        encoding="utf-8")
    target = work / f"seg-{number:02d}-{tag}.mp4"
    badge = _badge_filter(scene.get("badge"), width)
    title = ""
    if number == 1 and not vertical:
        title = (f",drawtext=fontfile={FONT}:text='Wegweiser Eyewitness':"
                 "fontsize=64:fontcolor=white:box=1:boxcolor=black@0.55:"
                 "boxborderw=18:x=(w-tw)/2:y=48")
    endcard = ""
    if number == 11:
        # The address sits above the caption with a clear gap, so
        # the caption box never covers it. Its colon is escaped for
        # the drawtext parser, which would cut the text at it.
        github_text = GITHUB_URL.replace(":", "\\:")
        endcard = (f",drawtext=fontfile={FONT_REG}:text='{github_text}':"
                   "fontsize=30:fontcolor=white:box=1:boxcolor=black@0.55:"
                   "boxborderw=12:x=(w-tw)/2:y=h-340")
    if is_phone and not vertical:
        caption_draw = (
            f",drawtext=fontfile={FONT_REG}:textfile={cap_file}:fontsize=30:"
            "fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=14:"
            "x=680:y=460:line_spacing=6")
    elif is_phone and vertical:
        caption_draw = (
            f",drawtext=fontfile={FONT_REG}:textfile={cap_file}:fontsize=30:"
            "fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=14:"
            "x=(w-tw)/2:y=1620:line_spacing=6")
    else:
        caption_draw = (
            f",drawtext=fontfile={FONT_REG}:textfile={cap_file}:fontsize=30:"
            "fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=14:"
            "x=(w-tw)/2:y=h-150:line_spacing=6")
    is_video = visual.suffix.lower() in (".mp4", ".webm", ".mov")
    if vertical and is_phone:
        # The phone sits at the top and the caption below it.
        vfilter = (f"scale=720:1500:force_original_aspect_ratio=decrease,"
                   f"pad=1080:1920:(ow-iw)/2:40,fps=30"
                   f"{badge}{title}{caption_draw}{endcard}")
    elif vertical:
        vfilter = (f"scale=1080:1920:force_original_aspect_ratio=increase,"
                   f"crop=1080:1920,fps=30{badge}{title}{caption_draw}{endcard}")
    elif is_phone:
        # The phone sits at the left and the caption beside it.
        vfilter = (f"scale=1920:1080:force_original_aspect_ratio=decrease,"
                   f"pad=1920:1080:120:(oh-ih)/2,fps=30"
                   f"{badge}{title}{caption_draw}{endcard}")
    else:
        vfilter = (f"scale=1920:1080:force_original_aspect_ratio=decrease,"
                   f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30"
                   f"{badge}{title}{caption_draw}{endcard}")
    afilter = ("silenceremove=start_periods=1:start_threshold=-45dB,"
               "loudnorm=I=-16:TP=-1.5:LRA=11,apad,atrim=0:"
               f"{duration},aformat=sample_rates=48000:channel_layouts=stereo")
    if is_video:
        cmd = ["ffmpeg", "-y", "-nostdin", "-protocol_whitelist", "file",
               "-stream_loop", "-1", "-i", str(visual),
               "-i", str(voice), "-t", str(duration),
               "-vf", vfilter, "-af", afilter,
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
               "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
               "-shortest", str(target)]
    else:
        cmd = ["ffmpeg", "-y", "-nostdin", "-protocol_whitelist", "file",
               "-loop", "1", "-i", str(visual),
               "-i", str(voice), "-t", str(duration),
               "-vf", vfilter, "-af", afilter,
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
               "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
               "-shortest", str(target)]
    _run(cmd, timeout=300)
    return target


def _join(segments: list[Path], target: Path) -> Path:
    """Join segments with crossfades. Falls back to a plain join."""
    if len(segments) == 1:
        _run(["ffmpeg", "-y", "-nostdin", "-protocol_whitelist", "file",
              "-i", str(segments[0]), "-c", "copy", str(target)])
        return target
    fade = 0.5
    durations = [_probe_duration(s) for s in segments]
    inputs: list[str] = []
    for seg in segments:
        inputs += ["-i", str(seg)]
    filters: list[str] = []
    # Video chain with xfade, audio chain with acrossfade.
    v_label, a_label = "0:v", "0:a"
    offset = durations[0] - fade if durations else 0.0
    for i in range(1, len(segments)):
        v_out, a_out = f"vx{i}", f"ax{i}"
        filters.append(
            f"[{v_label}][{i}:v]xfade=transition=fade:duration={fade}:"
            f"offset={offset:.3f}[{v_out}]")
        filters.append(f"[{a_label}][{i}:a]acrossfade=d={fade}[{a_out}]")
        v_label, a_label = v_out, a_out
        offset += durations[i] - fade if i < len(durations) else 0.0
    graph = ";".join(filters)
    try:
        _run(["ffmpeg", "-y", "-nostdin", "-protocol_whitelist", "file",
              *inputs, "-filter_complex", graph,
              "-map", f"[{v_label}]", "-map", f"[{a_label}]",
              "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
              "-c:a", "aac", "-b:a", "128k", str(target)], timeout=600)
        return target
    except Exception:
        # Plain join keeps a draft buildable when a fade graph fails.
        list_file = target.with_suffix(".txt")
        list_file.write_text(
            "".join(f"file '{s}'\n" for s in segments), encoding="utf-8")
        _run(["ffmpeg", "-y", "-nostdin", "-protocol_whitelist", "file",
              "-f", "concat", "-safe", "0", "-i", str(list_file),
              "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
              "-c:a", "aac", "-b:a", "128k", str(target)], timeout=600)
        return target


def build(demo_dir: Path, out: Path, *, vertical_only: bool = False,
          with_vertical: bool = False) -> dict:
    import yaml
    board = yaml.safe_load(
        (Path(__file__).with_name("storyboard.yaml")).read_text(
            encoding="utf-8"))
    scenes = board.get("scenes") or []
    for warning in caption_language_warnings(
            scenes, _voice_readings(demo_dir, scenes)):
        print(warning)
    total = sum(float(s["duration"]) for s in scenes)
    if total > MAX_TOTAL_S:
        raise SystemExit(
            f"storyboard runs {total:.0f}s, past the 2:58 limit")
    work = demo_dir / "build-work"
    work.mkdir(parents=True, exist_ok=True)
    timings: list[dict] = []
    if not vertical_only:
        segments = []
        clock = 0.0
        for scene in scenes:
            seg = _segment(scene, demo_dir, work, vertical=False)
            segments.append(seg)
            timings.append({"scene": int(scene["number"]),
                            "start": round(clock, 1),
                            "duration": float(scene["duration"])})
            clock += float(scene["duration"])
        out.parent.mkdir(parents=True, exist_ok=True)
        _join(segments, out)
        print(f"build_video: wrote {out} ({total:.0f}s)")
    if with_vertical or vertical_only:
        wanted = {1, 2, 5, 7, 9, 11}
        vsegs = [_segment(s, demo_dir, work, vertical=True)
                 for s in scenes if int(s["number"]) in wanted]
        vout = out.with_name(out.stem + "-vertical.mp4")
        _join(vsegs, vout)
        print(f"build_video: wrote {vout}")
    return {"total_s": total, "timings": timings, "out": str(out)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the demo video")
    parser.add_argument("--demo-dir",
                        default=os.environ.get("EYEWITNESS_DEMO_DIR", ""))
    parser.add_argument("--out", default="")
    parser.add_argument("--vertical", action="store_true",
                        help="also build the 1080 by 1920 cut")
    args = parser.parse_args()
    if not args.demo_dir:
        print("EYEWITNESS_DEMO_DIR not set")
        return 2
    demo_dir = Path(args.demo_dir)
    out = Path(args.out) if args.out else demo_dir / "draft" / "eyewitness-draft.mp4"
    build(demo_dir, out, with_vertical=args.vertical)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

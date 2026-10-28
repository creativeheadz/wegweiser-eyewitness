#!/usr/bin/env python3
"""Build the Eyewitness demo video with ffmpeg.

Reads storyboard.yaml beside this file. Each scene lasts its
voice-over plus a short lead-in and tail, so no visual sits
still: panels and overlays carry slow zooms, desktop takes are
sped to fit with a punch-in towards the named card, and phone
takes play beside their caption. Captions are the storyboard
wording, burned in with corner badges. Sound is set to -14 LUFS
with loudnorm and no music is added. The build fails when the
total runs past 2:58. With --vertical a second cut at 1080 by
1920 is made from scenes 1, 2, 5, 7, 9 and 11.

Scene 1 pushes in on the office panel, then cuts to the crash
screen with its percentage counting up. Scene 2 cuts between
three panels on the spoken lines, found by silence detection.
Scene 6 joins the filming to the accepted verdict through the
times the phone recorder saved. Scene 11 ends on an animated
card; its GitHub address sits above the caption.

Usage:
  EYEWITNESS_DEMO_DIR=/path/to/demo-media venv/bin/python \
    eyewitness/demo/build_video.py [--vertical] [--out path]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

GITHUB_URL = "https://github.com/creativeheadz/wegweiser-eyewitness"
MAX_TOTAL_S = 178  # 2:58
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
PRE_S = 0.4
POST_S = 1.5
FPS = 30


def _run(cmd: list[str], timeout: int = 600) -> None:
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


def scene_duration(scene: dict, demo_dir: Path) -> float:
    """Voice length plus the lead-in and tail for one scene."""
    voice_rel = str(scene.get("voiceover") or "")
    voice = demo_dir / voice_rel if voice_rel else None
    if voice is not None and voice.exists() and voice.stat().st_size > 0:
        base = _probe_duration(voice)
    else:
        base = float(scene.get("duration") or 10)
    duration = base + PRE_S + POST_S
    if int(scene.get("number") or 0) == 6:
        # The filming, its countdown and the verdict need room.
        duration = max(duration, 16.0)
    if int(scene.get("number") or 0) == 11:
        # The end card holds after the last spoken line.
        duration += 1.2
    return round(duration, 2)


def _caption_text(scene: dict, demo_dir: Path) -> str:
    """Caption wording for one scene: the storyboard wording."""
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


def _encode_base(cmd_inputs: list[str], vfilter: str, duration: float,
                 target: Path) -> Path:
    cmd = ["ffmpeg", "-y", "-nostdin", "-protocol_whitelist", "file",
           *cmd_inputs, "-vf", vfilter, "-t", f"{duration:.2f}",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
           "-an", str(target)]
    _run(cmd, timeout=600)
    return target


def _kb_filter(duration: float, fx: float = 0.5, fy: float = 0.5,
               z0: float = 1.02, z1: float = 1.16,
               size: str = "1920:1080") -> str:
    """A slow zoom towards one point of a still picture."""
    frames = max(int(duration * FPS), 1)
    return (f"scale=2048:-2,zoompan=z='{z0}+({z1}-{z0})*on/{frames}':"
            f"x='(iw-iw/zoom)*{fx}':y='(ih-ih/zoom)*{fy}':"
            f"d={frames}:s={size.replace(':', 'x')}:fps={FPS}")


def _panel_base(image: Path, duration: float, work: Path, name: str,
                *, fx: float = 0.5, fy: float = 0.5,
                z1: float = 1.16, vertical: bool = False) -> Path:
    target = work / name
    if vertical:
        frames = max(int(duration * FPS), 1)
        filt = (f"scale=3413:1920,zoompan=z='1.02+0.10*on/{frames}':"
                f"x='(iw-iw/zoom)*{fx}':y='(ih-ih/zoom)*{fy}':"
                f"d={frames}:s=1080x1920:fps={FPS}")
    else:
        filt = _kb_filter(duration, fx=fx, fy=fy, z1=z1)
    return _encode_base(["-i", str(image)], filt, duration, target)


def _concat_bases(parts: list[Path], target: Path) -> Path:
    graph_inputs: list[str] = []
    for part in parts:
        graph_inputs += ["-i", str(part)]
    filt = "".join(f"[{i}:v]" for i in range(len(parts)))
    filt += f"concat=n={len(parts)}:v=1:a=0,fps={FPS}[v]"
    cmd = ["ffmpeg", "-y", "-nostdin", "-protocol_whitelist", "file",
           *graph_inputs, "-filter_complex", filt, "-map", "[v]",
           "-c:v", "libx264",
           "-pix_fmt", "yuv420p", "-r", str(FPS), "-an", str(target)]
    _run(cmd, timeout=600)
    return target


def _crash_count_clip(crash: Path, duration: float, work: Path,
                      name: str) -> Path:
    """The crash screen with its percentage counting up to 87."""
    from PIL import Image, ImageDraw, ImageFont
    target = work / name
    with Image.open(crash) as img:
        base = img.convert("RGB")
    # Fill the frame, then find the percentage line by its bright
    # pixels so the redraw lands where the number sits.
    scale = max(1920 / base.size[0], 1080 / base.size[1])
    big = base.resize((int(base.size[0] * scale) + 2,
                       int(base.size[1] * scale) + 2))
    x0 = (big.size[0] - 1920) // 2
    y0 = (big.size[1] - 1080) // 2
    frame0 = big.crop((x0, y0, x0 + 1920, y0 + 1080))
    grey = frame0.convert("L")
    import numpy as np
    bright = np.asarray(grey) > 110
    rows = bright[:, 500:1420].sum(axis=1)
    bands: list[tuple[int, int]] = []
    start = None
    for i, count in enumerate(rows):
        if count > 12 and start is None:
            start = i
        elif count <= 12 and start is not None:
            bands.append((start, i))
            start = None
    # The percentage line is the band nearest 62 percent height.
    band = min(bands, key=lambda b: abs((b[0] + b[1]) / 2 - 670),
               default=(640, 700))
    cols = bright[band[0]:band[1], :].sum(axis=0)
    xs = [i for i, c in enumerate(cols) if c > 2]
    bx1 = max(min(xs) - 14, 0) if xs else 620
    bx2 = min(max(xs) + 14, 1920) if xs else 1300
    by1, by2 = band[0] - 10, band[1] + 10
    try:
        font = ImageFont.truetype(
            FONT_REG, max(int((by2 - by1) * 0.8), 20))
    except Exception:
        font = ImageFont.load_default()
    frames_dir = work / (name + "-frames")
    frames_dir.mkdir(parents=True, exist_ok=True)
    fps = 15
    total = max(int(duration * fps), 1)
    for i in range(total):
        t = i / fps
        frac = min(t / max(duration * 0.78, 0.1), 1.0)
        ease = 1 - (1 - frac) ** 2
        value = int(round(87 * ease))
        frame = frame0.copy()
        draw = ImageDraw.Draw(frame)
        draw.rectangle([bx1, by1, bx2, by2], fill=(0, 0, 0))
        label = f"{value}% complete"
        box = draw.textbbox((0, 0), label, font=font)
        tw = box[2] - box[0]
        cx = (bx1 + bx2) // 2
        cy = (by1 + by2) // 2 - (box[3] - box[1]) // 2 - box[1]
        draw.text((cx - tw // 2, cy), label, fill=(255, 255, 255),
                  font=font)
        frame.save(frames_dir / f"f{i:04d}.png", format="PNG")
    cmd = ["ffmpeg", "-y", "-nostdin", "-protocol_whitelist", "file",
           "-framerate", str(fps), "-i", str(frames_dir / "f%04d.png"),
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
           "-t", f"{duration:.2f}", str(target)]
    _run(cmd, timeout=300)
    return target


def _dialogue_cuts(voice: Path, duration: float) -> list[float]:
    """Cut times between spoken lines, from the silences."""
    done = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(voice), "-af",
         "silencedetect=noise=-38dB:d=0.22", "-f", "null", "-"],
        capture_output=True, text=True, timeout=120)
    starts = [float(m) for m in
              re.findall(r"silence_start: ([0-9.]+)", done.stderr or "")]
    ends = [float(m) for m in
            re.findall(r"silence_end: ([0-9.]+)", done.stderr or "")]
    mids = [(a + b) / 2 for a, b in zip(starts, ends)
            if b - a > 0.3 and a > 0.8]
    cuts: list[float] = []
    for mid in mids:
        if not cuts or mid - cuts[-1] > 0.9:
            cuts.append(mid)
        if len(cuts) == 2:
            break
    if len(cuts) < 2:
        return [duration * 0.34, duration * 0.58]
    return cuts


def _scene1_base(demo_dir: Path, duration: float, work: Path,
                 *, vertical: bool = False) -> Path:
    tag = "v" if vertical else "h"
    office = demo_dir / "panels" / "scene01-office.png"
    if vertical:
        return _panel_base(office, duration, work,
                           f"base-01-{tag}.mp4", fx=0.55, fy=0.45,
                           vertical=True)
    first = duration * 0.52
    part_a = _panel_base(office, first, work, f"base-01a-{tag}.mp4",
                         fx=0.60, fy=0.42, z1=1.22)
    crash = _find_crash(demo_dir)
    part_b = _crash_count_clip(crash, duration - first, work,
                               f"base-01b-{tag}.mp4")
    return _concat_bases([part_a, part_b], work / f"base-01-{tag}.mp4")


def _find_crash(demo_dir: Path) -> Path:
    name = "windows11-crash-screen-0x1A.png"
    for cand in (demo_dir / "golden" / name,
                 demo_dir.parent / "golden" / name):
        if cand.exists():
            return cand
    raise SystemExit("build_video: golden crash screen not found")


def _scene2_base(scene: dict, demo_dir: Path, duration: float,
                 work: Path, *, vertical: bool = False) -> Path:
    tag = "v" if vertical else "h"
    voice = demo_dir / str(scene.get("voiceover") or "")
    cuts = _dialogue_cuts(voice, duration) if voice.exists() else \
        [duration * 0.34, duration * 0.58]
    # The voice starts after the lead-in; panels cut with it.
    cuts = [c + PRE_S for c in cuts]
    panels = [demo_dir / "panels" / "scene01-office.png",
              demo_dir / "panels" / "scene02-tech.png",
              demo_dir / "panels" / "scene02-brenda.png"]
    if vertical:
        return _panel_base(panels[2], duration, work,
                           f"base-02-{tag}.mp4", fx=0.5, fy=0.4,
                           vertical=True)
    bounds = [0.0, *cuts, duration]
    parts = []
    for i, panel in enumerate(panels):
        span = max(bounds[i + 1] - bounds[i], 0.6)
        parts.append(_panel_base(
            panel, span, work, f"base-02{chr(97 + i)}-{tag}.mp4",
            fx=0.45 + 0.08 * i, fy=0.42, z1=1.13))
    return _concat_bases(parts, work / f"base-02-{tag}.mp4")


_END_CARD_S = 3.6


def _end_card_clip(duration: float, work: Path, name: str) -> Path:
    from PIL import Image, ImageDraw, ImageFont
    target = work / name
    frames_dir = work / (name + "-frames")
    frames_dir.mkdir(parents=True, exist_ok=True)
    try:
        big = ImageFont.truetype(FONT, 92)
        mid = ImageFont.truetype(FONT_REG, 44)
        small = ImageFont.truetype(FONT_REG, 30)
    except Exception:
        big = mid = small = ImageFont.load_default()
    lines = [
        ("Wegweiser Eyewitness", big, (255, 255, 255), 330),
        ("When the agent goes dark, the user's phone becomes "
         "the sensor.", mid, (255, 205, 140), 480),
        ("Muse Spark · SAM 3.1 · Muse Voice Transcribe · "
         "Muse Image · built with Muse Code", small,
         (190, 190, 190), 590),
    ]
    fps = 15
    total = max(int(duration * fps), 1)
    for i in range(total):
        t = i / fps
        frame = Image.new("RGB", (1920, 1080), (22, 24, 28))
        draw = ImageDraw.Draw(frame, "RGBA")
        for text, font, colour, y in lines:
            start = 0.25 + 0.45 * lines.index((text, font, colour, y))
            frac = min(max((t - start) / 0.7, 0.0), 1.0)
            if frac <= 0:
                continue
            alpha = int(255 * frac)
            rise = int((1 - frac) * 26)
            box = draw.textbbox((0, 0), text, font=font)
            tw = box[2] - box[0]
            draw.text(((1920 - tw) // 2, y + rise), text,
                      fill=colour + (alpha,), font=font)
        frame.save(frames_dir / f"f{i:04d}.png", format="PNG")
    cmd = ["ffmpeg", "-y", "-nostdin", "-protocol_whitelist", "file",
           "-framerate", str(fps), "-i", str(frames_dir / "f%04d.png"),
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
           "-t", f"{duration:.2f}", str(target)]
    _run(cmd, timeout=300)
    return target


def _scene11_base(demo_dir: Path, duration: float, work: Path,
                  *, vertical: bool = False) -> Path:
    tag = "v" if vertical else "h"
    panel = demo_dir / "panels" / "scene11-brenda.png"
    if vertical:
        return _panel_base(panel, duration, work,
                           f"base-11-{tag}.mp4", fx=0.5, fy=0.4,
                           vertical=True)
    first = max(duration - _END_CARD_S, 1.0)
    part_a = _panel_base(panel, first, work, f"base-11a-{tag}.mp4",
                         fx=0.45, fy=0.42, z1=1.12)
    part_b = _end_card_clip(_END_CARD_S, work, f"base-11b-{tag}.mp4")
    return _concat_bases([part_a, part_b], work / f"base-11-{tag}.mp4")


# Punch-in targets per desktop scene, as frame fractions.
_DESKTOP_FOCUS = {3: (0.52, 0.42), 4: (0.50, 0.52), 10: (0.50, 0.48)}


def _desktop_base(take: Path, duration: float, work: Path, name: str,
                  *, fx: float = 0.5, fy: float = 0.5) -> Path:
    """Fit a desktop take to the scene with a slow punch-in.

    Takes are recorded at a 1280 by 720 viewport on a 1920 by
    1080 canvas, so the page is cropped out first, then sped to
    fit and zoomed towards the card the voice names.
    """
    take_dur = _probe_duration(take)
    speed = max(take_dur / max(duration, 0.1), 0.05) if take_dur else 1.0
    frames = max(int(duration * FPS), 1)
    filt = (f"trim=start=1.2,setpts=(PTS-STARTPTS)/{speed:.4f},"
            f"crop=1280:720:0:0,fps={FPS},scale=1600:900,"
            f"zoompan=z='1.0+0.30*on/{frames}':"
            f"x='(iw-iw/zoom)*{fx}':y='(ih-ih/zoom)*{fy}':"
            f"d=1:s=1920x1080:fps={FPS}")
    return _encode_base(["-i", str(take)], filt, duration,
                        work / name)


def _phone_composite(scene: dict, take: Path, demo_dir: Path,
                     duration: float, work: Path) -> Path:
    """The phone source for one scene, joined where it is two acts.

    Scene 6 films, hears a retake on the recorder file, then
    sends the same clip as a file. The edit cuts from the stop
    of the recording to the accepted verdict, using the times
    the recorder saved.
    """
    if int(scene.get("number") or 0) != 6:
        return take
    sidecar = demo_dir / "phone" / "scene06-times.json"
    if not sidecar.exists():
        return take
    try:
        marks = json.loads(sidecar.read_text(encoding="utf-8"))
        retake = float(marks["retake_s"])
        got_it = float(marks["got_it_s"])
    except (ValueError, KeyError, TypeError, OSError):
        return take
    # The recording starts a breath before the recorder's clock.
    # The head ends while the page still sends, before the
    # retake line lands; detection of that line lags it.
    head_end = retake + 0.8 - 2.2
    got_it += 0.8
    tail = 3.4
    head_dur = max(duration - tail, 1.0)
    head_speed = max(head_end / head_dur, 0.05)
    joined = work / "base-06-joined.mp4"
    filt = (f"[0:v]trim=start=0:end={head_end:.2f},"
            f"setpts=(PTS-STARTPTS)/{head_speed:.4f}[a];"
            f"[0:v]trim=start={max(got_it - 2.4, 0):.2f}:"
            f"end={got_it + 1.6:.2f},setpts=PTS-STARTPTS[b];"
            f"[a][b]concat=n=2:v=1:a=0,fps={FPS}")
    cmd = ["ffmpeg", "-y", "-nostdin", "-protocol_whitelist", "file",
           "-i", str(take), "-filter_complex", filt,
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
           "-an", "-t", f"{duration:.2f}", str(joined)]
    _run(cmd, timeout=600)
    return joined


def _phone_base(take: Path, duration: float, work: Path, name: str,
                *, vertical: bool = False) -> Path:
    take_dur = _probe_duration(take)
    speed = 1.0
    if take_dur and take_dur > duration:
        speed = min(take_dur / duration, 3.2)
    if vertical:
        filt = (f"setpts=PTS/{speed:.4f},fps={FPS},"
                f"scale=693:1500:force_original_aspect_ratio=decrease,"
                f"pad=1080:1920:(ow-iw)/2:30:color=0x14161a")
    else:
        filt = (f"setpts=PTS/{speed:.4f},fps={FPS},"
                f"scale=462:1000:force_original_aspect_ratio=decrease,"
                f"pad=1920:1080:110:(oh-ih)/2:color=0x14161a")
    return _encode_base(["-i", str(take)], filt, duration, work / name)


def _overlay_base(take: Path, duration: float, work: Path, name: str,
                  *, vertical: bool = False) -> Path:
    take_dur = _probe_duration(take)
    speed = (take_dur / duration) if take_dur else 1.0
    if vertical:
        filt = (f"setpts=PTS/{speed:.4f},fps={FPS},"
                f"scale=1080:1920:force_original_aspect_ratio=decrease,"
                f"pad=1080:1920:(ow-iw)/2:280:color=0x121212")
    else:
        filt = (f"setpts=PTS/{speed:.4f},fps={FPS},"
                f"scale=1920:1080:force_original_aspect_ratio=increase,"
                f"crop=1920:1080")
    return _encode_base(["-stream_loop", "1" if speed < 1 else "0",
                         "-i", str(take)], filt, duration, work / name)


def _base_for(scene: dict, demo_dir: Path, work: Path, duration: float,
              *, vertical: bool = False) -> Path:
    number = int(scene["number"])
    tag = "v" if vertical else "h"
    visual_rel = str(scene.get("visual") or "")
    visual = demo_dir / visual_rel
    if not visual.exists():
        card = work / f"card-{number:02d}.png"
        size = (1080, 1920) if vertical else (1920, 1080)
        visual = _placeholder_card(scene, card, size=size)
        return _panel_base(visual, duration, work,
                           f"base-{number:02d}-{tag}.mp4",
                           vertical=vertical)
    if number == 1:
        return _scene1_base(demo_dir, duration, work, vertical=vertical)
    if number == 2:
        return _scene2_base(scene, demo_dir, duration, work,
                            vertical=vertical)
    if number == 11:
        return _scene11_base(demo_dir, duration, work, vertical=vertical)
    if visual_rel.startswith("desktop/"):
        fx, fy = _DESKTOP_FOCUS.get(number, (0.5, 0.5))
        return _desktop_base(visual, duration, work,
                             f"base-{number:02d}-{tag}.mp4",
                             fx=fx, fy=fy)
    if visual_rel.startswith("phone/"):
        source = _phone_composite(scene, visual, demo_dir, duration,
                                  work)
        return _phone_base(source, duration, work,
                           f"base-{number:02d}-{tag}.mp4",
                           vertical=vertical)
    if visual_rel.startswith("overlays/"):
        return _overlay_base(visual, duration, work,
                             f"base-{number:02d}-{tag}.mp4",
                             vertical=vertical)
    if visual.suffix.lower() in (".mp4", ".webm", ".mov"):
        return _overlay_base(visual, duration, work,
                             f"base-{number:02d}-{tag}.mp4",
                             vertical=vertical)
    return _panel_base(visual, duration, work,
                       f"base-{number:02d}-{tag}.mp4",
                       vertical=vertical)


def _segment(scene: dict, demo_dir: Path, work: Path, duration: float,
             *, vertical: bool = False) -> Path:
    """One scene as a finished segment with voice and caption."""
    number = int(scene["number"])
    width, height = (1080, 1920) if vertical else (1920, 1080)
    base = _base_for(scene, demo_dir, work, duration, vertical=vertical)
    voice_rel = str(scene.get("voiceover") or "")
    voice = demo_dir / voice_rel if voice_rel else None
    if voice is None or not voice.exists():
        voice = demo_dir / "voice-placeholder" / \
            f"scene{number:02d}-placeholder-flite.wav"
        _flite_voice(str(scene.get("caption") or ""), voice)
    caption = _caption_text(scene, demo_dir)
    visual_rel = str(scene.get("visual") or "")
    is_phone = visual_rel.startswith("phone/")
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
        start = max(duration - _END_CARD_S, 0.0)
        endcard = (f",drawtext=fontfile={FONT_REG}:text='{github_text}':"
                   "fontsize=30:fontcolor=white:box=1:boxcolor=black@0.55:"
                   f"boxborderw=12:x=(w-tw)/2:y=h-340:"
                   f"enable='gte(t,{start:.2f})'")
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
    vfilter = f"fps={FPS}{badge}{title}{caption_draw}{endcard}"
    afilter = ("adelay=400|400,loudnorm=I=-14:TP=-1.5:LRA=11,apad,"
               f"atrim=0:{duration:.2f},"
               "aformat=sample_rates=48000:channel_layouts=stereo")
    cmd = ["ffmpeg", "-y", "-nostdin", "-protocol_whitelist", "file",
           "-i", str(base), "-i", str(voice),
           "-vf", vfilter, "-af", afilter, "-t", f"{duration:.2f}",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
           "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
           str(target)]
    _run(cmd, timeout=600)
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
              "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
              "-c:a", "aac", "-b:a", "128k", str(target)], timeout=900)
        return target
    except Exception:
        # Plain join keeps a draft buildable when a fade graph fails.
        list_file = target.with_suffix(".txt")
        list_file.write_text(
            "".join(f"file '{s}'\n" for s in segments), encoding="utf-8")
        _run(["ffmpeg", "-y", "-nostdin", "-protocol_whitelist", "file",
              "-f", "concat", "-safe", "0", "-i", str(list_file),
              "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
              "-c:a", "aac", "-b:a", "128k", str(target)], timeout=900)
        return target


def _loudness_fix(path: Path) -> None:
    """Bring the joined cut to -14 LUFS with one gain step.

    Each segment is normalised on its own first. The joined
    file is measured once and gained by the difference, which
    keeps the balance between scenes as the segments set it.
    """
    done = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af",
         "loudnorm=print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, timeout=600)
    match = re.search(r'"input_i"\s*:\s*"(-?[0-9.]+)"',
                      done.stderr or "")
    if not match:
        print("build_video: loudness measure failed, leaving as is")
        return
    gain = -14.0 - float(match.group(1))
    if abs(gain) < 0.3:
        print(f"build_video: loudness already {match.group(1)} LUFS")
        return
    fixed = path.with_suffix(".loud.mp4")
    _run(["ffmpeg", "-y", "-nostdin", "-protocol_whitelist", "file",
          "-i", str(path), "-c:v", "copy",
          "-af", f"volume={gain:.2f}dB",
          "-c:a", "aac", "-b:a", "128k", str(fixed)], timeout=600)
    fixed.replace(path)
    print(f"build_video: loudness gained {gain:+.2f} dB to -14 LUFS")


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
    durations = {int(s["number"]): scene_duration(s, demo_dir)
                 for s in scenes}
    total = sum(durations.values())
    if total > MAX_TOTAL_S:
        raise SystemExit(
            f"scenes run {total:.0f}s, past the 2:58 limit")
    work = demo_dir / "build-work"
    work.mkdir(parents=True, exist_ok=True)
    timings: list[dict] = []
    if not vertical_only:
        segments = []
        clock = 0.0
        for scene in scenes:
            dur = durations[int(scene["number"])]
            seg = _segment(scene, demo_dir, work, dur, vertical=False)
            segments.append(seg)
            timings.append({"scene": int(scene["number"]),
                            "start": round(clock, 1),
                            "duration": round(dur, 1)})
            clock += dur
        out.parent.mkdir(parents=True, exist_ok=True)
        _join(segments, out)
        _loudness_fix(out)
        print(f"build_video: wrote {out} ({total:.0f}s of scenes)")
    if with_vertical or vertical_only:
        wanted = {1, 2, 5, 7, 9, 11}
        vsegs = [_segment(s, demo_dir, work,
                          durations[int(s["number"])], vertical=True)
                 for s in scenes if int(s["number"]) in wanted]
        vout = out.with_name(out.stem + "-vertical.mp4")
        _join(vsegs, vout)
        _loudness_fix(vout)
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
    out = Path(args.out) if args.out else \
        demo_dir / "final" / "eyewitness-final.mp4"
    result = build(demo_dir, out, with_vertical=args.vertical)
    for row in result["timings"]:
        print(f"build_video: scene {row['scene']:02d} "
              f"start {row['start']:.1f}s length {row['duration']:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

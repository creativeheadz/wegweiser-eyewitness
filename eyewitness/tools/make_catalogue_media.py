#!/usr/bin/env python3
"""Render the Phase 7 catalogue synthetic media into a folder.

Usage: venv/bin/python eyewitness/tools/make_catalogue_media.py FOLDER

Writes a printed test page with vertical streaks and a faint cyan
block, a monitor in its frame showing a No Signal HDMI 1 box on
black, a router front with labelled lights, and a beep recording
(one long, two short) as WAV, WebM Opus and M4A AAC. Every image
passes the server quality gate. Synthetic only, never committed.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _font(size: int):
    from PIL import ImageFont
    for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def test_page(path: Path) -> None:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1200, 1600), (245, 245, 240))
    d = ImageDraw.Draw(img)
    d.rectangle([60, 60, 1140, 1540], outline=(30, 30, 30), width=4)
    d.text((90, 90), "PRINTER TEST PAGE", fill=(10, 10, 10), font=_font(52))
    d.text((90, 170), "Model: ColourJet CJ-410 (synthetic)", fill=(10, 10, 10), font=_font(34))
    d.text((90, 220), "Page count: 12,480", fill=(10, 10, 10), font=_font(34))
    d.text((90, 270), "Toner: black 62%, cyan 18%, magenta 44%, yellow 51%",
           fill=(10, 10, 10), font=_font(30))
    blocks = [("C", (0, 174, 239)), ("M", (236, 0, 140)),
              ("Y", (255, 242, 0)), ("K", (20, 20, 20))]
    x = 90
    for label, colour in blocks:
        faint = (200, 235, 245) if label == "C" else colour
        d.rectangle([x, 360, x + 220, 620], fill=faint, outline=(0, 0, 0), width=3)
        d.text((x + 90, 640), label, fill=(10, 10, 10), font=_font(48))
        x += 250
    for y in range(760, 1400, 46):
        d.text((90, y), "The quick brown fox jumps over the lazy dog 0123456789",
               fill=(25, 25, 25), font=_font(30))
    # Vertical streaks down the page.
    for sx in (420, 428, 700, 706, 980):
        d.rectangle([sx, 340, sx + 5, 1460], fill=(120, 120, 120))
    img.save(path, format="PNG")


def display(path: Path) -> None:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1400, 1000), (70, 70, 75))
    d = ImageDraw.Draw(img)
    d.rectangle([120, 90, 1280, 830], fill=(25, 25, 28), outline=(10, 10, 10), width=10)
    d.rectangle([150, 120, 1250, 800], fill=(5, 5, 8))
    d.rectangle([480, 380, 920, 540], fill=(30, 30, 36), outline=(200, 200, 200), width=4)
    d.text((540, 410), "No Signal", fill=(255, 255, 255), font=_font(64))
    d.text((600, 485), "HDMI 1", fill=(220, 220, 220), font=_font(40))
    d.text((560, 850), "ViewPanel VP-240 (synthetic)", fill=(15, 15, 15), font=_font(34))
    d.rectangle([660, 880, 740, 960], fill=(40, 40, 44))
    img.save(path, format="PNG")


def router(path: Path) -> None:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1400, 800), (210, 210, 205))
    d = ImageDraw.Draw(img)
    d.rectangle([120, 180, 1280, 620], fill=(35, 35, 40), outline=(10, 10, 10), width=6)
    d.text((170, 220), "RouteBox RB-120 (synthetic)", fill=(235, 235, 235), font=_font(36))
    lights = [("Power", (40, 200, 80)), ("Internet", (230, 50, 40)),
              ("Wi-Fi", (40, 200, 80)), ("LAN", (55, 55, 60))]
    x = 190
    for label, colour in lights:
        d.ellipse([x, 330, x + 90, 420], fill=colour, outline=(220, 220, 220), width=3)
        d.text((x - 8, 440), label, fill=(235, 235, 235), font=_font(34))
        x += 260
    d.text((170, 540), "Status lights", fill=(200, 200, 200), font=_font(28))
    img.save(path, format="PNG")


def beeps_wav(path: Path) -> None:
    import wave
    import numpy as np
    sr = 16000

    def tone(freq, dur, square=False):
        t = np.arange(int(sr * dur)) / sr
        wave_ = np.sign(np.sin(2 * math.pi * freq * t)) * 0.6 if square \
            else np.sin(2 * math.pi * freq * t) * 0.6
        return (wave_ * 32767).astype(np.int16)

    gap = np.zeros(int(sr * 0.35), dtype=np.int16)
    sig = np.concatenate([tone(880, 0.8), gap, tone(1320, 0.25, square=True),
                          gap, tone(1320, 0.25), np.zeros(int(sr * 0.6), dtype=np.int16)])
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sr)
        wav.writeframes(sig.tobytes())


def convert(src: Path, dst: Path, args: list[str]) -> None:
    done = subprocess.run(["ffmpeg", "-y", "-nostdin", "-protocol_whitelist", "file",
                           "-i", str(src), *args, str(dst)],
                          capture_output=True, timeout=60)
    if done.returncode != 0:
        raise SystemExit(f"ffmpeg failed for {dst.name}")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    folder = Path(sys.argv[1])
    folder.mkdir(parents=True, exist_ok=True)
    test_page(folder / "synthetic-printer-test-page.png")
    display(folder / "synthetic-display-no-signal.png")
    router(folder / "synthetic-router-lights.png")
    beeps_wav(folder / "synthetic-beeps.wav")
    convert(folder / "synthetic-beeps.wav", folder / "synthetic-beeps.webm",
            ["-c:a", "libopus", "-b:a", "32k"])
    convert(folder / "synthetic-beeps.wav", folder / "synthetic-beeps.m4a",
            ["-c:a", "aac", "-b:a", "64k"])
    from app.eyewitness.readers.quality import assess_image
    for name in ("synthetic-printer-test-page.png", "synthetic-display-no-signal.png",
                 "synthetic-router-lights.png"):
        result = assess_image((folder / name).read_bytes())
        print(f"{name}: quality {result['verdict']} {result['quality']}")
        if result["verdict"] != "ok":
            return 1
    print(f"wrote catalogue media to {folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

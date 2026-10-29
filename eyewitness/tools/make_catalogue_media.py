#!/usr/bin/env python3
"""Render the Phase 7 catalogue synthetic media into a folder.

Usage: venv/bin/python eyewitness/tools/make_catalogue_media.py FOLDER

Writes a printed test page with vertical streaks and a faint cyan
block, a clean test page as the negative, a monitor in its frame
showing a No Signal HDMI 1 box on black, a router front with
labelled lights, a printer panel showing a paper jam, and a beep
recording (one long, two short) as WAV, WebM Opus and M4A AAC, plus
a quiet WebM at the level a phone hears with its gain control off,
over fan noise. Every image passes the server quality gate.
Synthetic only, never committed.
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


def test_page(path: Path, *, faulty: bool = True) -> None:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1200, 1600), (245, 245, 240))
    d = ImageDraw.Draw(img)
    d.rectangle([60, 60, 1140, 1540], outline=(30, 30, 30), width=4)
    d.text((90, 90), "PRINTER TEST PAGE", fill=(10, 10, 10), font=_font(52))
    d.text((90, 170), "Model: ColourJet CJ-410 (synthetic)", fill=(10, 10, 10), font=_font(34))
    d.text((90, 220), "Page count: 12,480", fill=(10, 10, 10), font=_font(34))
    toner = ("Toner: black 62%, cyan 18%, magenta 44%, yellow 51%" if faulty
             else "Toner: black 71%, cyan 66%, magenta 58%, yellow 63%")
    d.text((90, 270), toner, fill=(10, 10, 10), font=_font(30))
    blocks = [("C", (0, 174, 239)), ("M", (236, 0, 140)),
              ("Y", (255, 242, 0)), ("K", (20, 20, 20))]
    x = 90
    for label, colour in blocks:
        faint = (200, 235, 245) if (faulty and label == "C") else colour
        d.rectangle([x, 360, x + 220, 620], fill=faint, outline=(0, 0, 0), width=3)
        d.text((x + 90, 640), label, fill=(10, 10, 10), font=_font(48))
        x += 250
    for y in range(760, 1400, 46):
        d.text((90, y), "The quick brown fox jumps over the lazy dog 0123456789",
               fill=(25, 25, 25), font=_font(30))
    # Vertical streaks down the page.
    if faulty:
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
    d.text((585, 803), "ViewPanel VP-240 (synthetic)", fill=(150, 150, 155), font=_font(22))
    d.rectangle([660, 830, 740, 960], fill=(40, 40, 44))
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


def printer_panel(path: Path) -> None:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1400, 900), (226, 224, 218))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([110, 150, 1290, 760], radius=40, fill=(52, 54, 58))
    d.text((170, 190), "LaserCo LP-300 (synthetic)", fill=(225, 225, 225), font=_font(32))
    # The display: two lines and an event code.
    d.rectangle([170, 270, 840, 560], fill=(150, 196, 120), outline=(20, 20, 20), width=6)
    d.text((205, 300), "Paper jam", fill=(15, 30, 10), font=_font(58))
    d.text((205, 390), "Open tray 2", fill=(15, 30, 10), font=_font(44))
    d.text((205, 470), "13.B2.D1", fill=(15, 30, 10), font=_font(44))
    # Status lights: Ready off, Attention amber.
    for x, label, colour in ((960, "Ready", (70, 72, 76)), (1120, "Attention", (245, 160, 30))):
        d.ellipse([x, 330, x + 70, 400], fill=colour, outline=(200, 200, 200), width=3)
        d.text((x - 30, 420), label, fill=(225, 225, 225), font=_font(28))
    for x in range(960, 1240, 100):
        d.rounded_rectangle([x, 560, x + 80, 640], radius=12, fill=(80, 82, 88))
    img.save(path, format="PNG")


def beeps_quiet_webm(path: Path) -> None:
    """One long and two short square tones at 0.03 of full scale over
    fan noise, at 48 kHz, as Chrome records with gain control off."""
    import numpy as np
    rate = 48000
    rng = np.random.default_rng(7)
    sig = np.convolve(rng.normal(0, 1, rate * 12), np.ones(24) / 24, mode="same")
    sig *= 0.003 / float(np.sqrt(np.mean(sig ** 2)))
    at = 2.0
    for dur in (0.8, 0.2, 0.2):
        t = np.arange(int(rate * dur)) / rate
        ramp = np.minimum(1.0, np.minimum(t, dur - t) / 0.005)
        start = int(at * rate)
        sig[start:start + t.size] += 0.03 * np.sign(np.sin(2 * math.pi * 2000 * t)) * ramp
        at += dur + 0.35
    wav = path.with_suffix(".tmp.wav")
    _write_wav(wav, sig, rate)
    convert(wav, path, ["-c:a", "libopus", "-b:a", "32k"])
    wav.unlink()


def _write_wav(path: Path, samples, rate: int) -> None:
    import wave
    import numpy as np
    data = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(data.tobytes())


def beeps_wav(path: Path) -> None:
    import wave
    import numpy as np
    sr = 16000

    def tone(freq, dur, square=False):
        t = np.arange(int(sr * dur)) / sr
        wave_ = np.sign(np.sin(2 * math.pi * freq * t)) * 0.6 if square \
            else np.sin(2 * math.pi * freq * t) * 0.6
        return (wave_ * 32767).astype(np.int16)

    # A PC speaker sounds every beep of a pattern at one pitch.
    gap = np.zeros(int(sr * 0.35), dtype=np.int16)
    sig = np.concatenate([tone(1000, 0.8), gap, tone(1000, 0.25, square=True),
                          gap, tone(1000, 0.25), np.zeros(int(sr * 0.6), dtype=np.int16)])
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
    test_page(folder / "synthetic-printer-test-page-clean.png", faulty=False)
    display(folder / "synthetic-display-no-signal.png")
    router(folder / "synthetic-router-lights.png")
    printer_panel(folder / "synthetic-printer-panel.png")
    beeps_wav(folder / "synthetic-beeps.wav")
    convert(folder / "synthetic-beeps.wav", folder / "synthetic-beeps.webm",
            ["-c:a", "libopus", "-b:a", "32k"])
    convert(folder / "synthetic-beeps.wav", folder / "synthetic-beeps.m4a",
            ["-c:a", "aac", "-b:a", "64k"])
    beeps_quiet_webm(folder / "synthetic-beeps-quiet.webm")
    from app.eyewitness.readers.quality import assess_image
    for name in ("synthetic-printer-test-page.png", "synthetic-printer-test-page-clean.png",
                 "synthetic-display-no-signal.png", "synthetic-router-lights.png",
                 "synthetic-printer-panel.png"):
        result = assess_image((folder / name).read_bytes())
        print(f"{name}: quality {result['verdict']} {result['quality']}")
        if result["verdict"] != "ok":
            return 1
    print(f"wrote catalogue media to {folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

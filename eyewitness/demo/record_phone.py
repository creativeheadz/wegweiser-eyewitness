#!/usr/bin/env python3
"""Phone recordings for demo scenes 5 and 6.

Playwright with mobile emulation at 390 by 844 opens the customer
page whose address comes from an argument. Chrome fake camera is fed
from synthetic media converted to y4m, so the live camera, the
countdown and the verdict show in the recording. Video lands in the
demo folder. No address is held in this file.

Usage:
  venv/bin/python eyewitness/demo/record_phone.py \
    --url <customer page address> --scene 5 \
    --fake-source <clip.mp4 or photo.png>
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def to_y4m(source: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
        cmd = ["ffmpeg", "-y", "-nostdin", "-protocol_whitelist", "file",
               "-loop", "1", "-i", str(source), "-t", "20",
               "-vf", "scale=640:480:force_original_aspect_ratio=decrease,"
                      "pad=640:480:(ow-iw)/2:(oh-ih)/2",
               "-pix_fmt", "yuv420p", str(target)]
    else:
        cmd = ["ffmpeg", "-y", "-nostdin", "-protocol_whitelist", "file",
               "-stream_loop", "-1", "-i", str(source), "-t", "20",
               "-vf", "scale=640:480:force_original_aspect_ratio=decrease,"
                      "pad=640:480:(ow-iw)/2:(oh-ih)/2",
               "-pix_fmt", "yuv420p", str(target)]
    done = subprocess.run(cmd, capture_output=True, timeout=120)
    if done.returncode != 0:
        raise RuntimeError("y4m conversion failed")
    return target


def record(url: str, scene: int, demo_dir: Path,
           fake_source: Path | None) -> Path:
    out_dir = demo_dir / "phone"
    out_dir.mkdir(parents=True, exist_ok=True)
    y4m = None
    if fake_source is not None:
        y4m = to_y4m(fake_source, out_dir / f"fake-scene{scene:02d}.y4m")
    from playwright.sync_api import sync_playwright
    chrome_args = ["--no-sandbox",
                   "--use-fake-ui-for-media-stream",
                   "--use-fake-device-for-media-stream"]
    if y4m is not None:
        chrome_args.append(f"--use-file-for-fake-video-capture={y4m}")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path="/usr/bin/google-chrome", args=chrome_args)
        context = browser.new_context(
            viewport={"width": 390, "height": 844}, is_mobile=True,
            has_touch=True, device_scale_factor=2,
            record_video_dir=str(out_dir),
            record_video_size={"width": 390, "height": 844},
            permissions=["camera", "microphone"])
        page = context.new_page()
        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(1200)
        try:
            if scene == 6:
                nxt = page.locator("[data-nav-next]")
                if nxt.count():
                    nxt.first.click()
                    page.wait_for_timeout(600)
            opener = page.locator("[data-start-camera]:visible")
            if opener.count():
                opener.first.click()
                page.wait_for_timeout(1500)
            if scene == 5:
                take = page.locator("[data-take-photo]:visible")
                if take.count() and take.first.is_visible():
                    take.first.click()
                    page.wait_for_timeout(2500)
            else:
                rec = page.locator("[data-record-video]:visible")
                if rec.count() and rec.first.is_visible():
                    rec.first.click()
                    # Countdown plus the clip itself.
                    page.wait_for_timeout(14000)
            # Give the verdict poll a chance to land.
            page.wait_for_timeout(3000)
        except Exception as exc:
            print(f"record_phone: step note: {exc}")
        video_path = None
        try:
            video_path = page.video.path() if page.video else None
        except Exception:
            video_path = None
        context.close()
        browser.close()
    target = out_dir / f"scene{scene:02d}.webm"
    if video_path and Path(video_path).exists():
        Path(video_path).replace(target)
        print(f"record_phone: wrote {target}")
    else:
        # Playwright names the file by page id; take the newest.
        webs = sorted(out_dir.glob("*.webm"), key=lambda p: p.stat().st_mtime)
        if webs:
            webs[-1].replace(target)
            print(f"record_phone: wrote {target}")
        else:
            print("record_phone: no video file produced")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Phone demo recordings")
    parser.add_argument("--url", required=True,
                        help="customer page address")
    parser.add_argument("--scene", type=int, default=5, choices=(5, 6))
    parser.add_argument("--demo-dir",
                        default=os.environ.get("EYEWITNESS_DEMO_DIR", ""))
    parser.add_argument("--fake-source", default="",
                        help="synthetic clip or photo for the fake camera")
    args = parser.parse_args()
    if not args.demo_dir:
        print("EYEWITNESS_DEMO_DIR not set")
        return 2
    source = Path(args.fake_source) if args.fake_source else None
    record(args.url, args.scene, Path(args.demo_dir), source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

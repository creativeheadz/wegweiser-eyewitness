#!/usr/bin/env python3
"""Phone recordings for demo scenes 5 and 6.

Playwright with mobile emulation at 390 by 844 opens the customer
page whose address comes from an argument or the demo ticket env
file. Chrome's fake camera is fed from media converted to y4m, so
the live preview, the countdown and the verdict show in the
recording. A tap indicator ripples where the script taps. No
address is held in this file and none is printed.

Scene 5 walks to the photo step and takes the crash photo twice:
the feed starts out of focus, the page asks for a retake, then the
feed is sharp and the page accepts. Scene 6 films the power light
from the blink clip through the countdown to the verdict.

Usage:
  venv/bin/python eyewitness/demo/record_phone.py --scene 5 \
    --crash <crash.png>
  venv/bin/python eyewitness/demo/record_phone.py --scene 6 \
    --fake-source <blink.mp4>
  venv/bin/python eyewitness/demo/record_phone.py --finish
  venv/bin/python eyewitness/demo/record_phone.py --wait-triage
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

WG_BASE = os.environ.get("EYEWITNESS_WG_URL", "")

TAP_SCRIPT = r"""
(() => {
  const css = `
    .ew-tap { position: fixed; z-index: 2147483647; width: 16px;
      height: 16px; margin: -8px 0 0 -8px; border-radius: 50%;
      background: rgba(230,120,40,0.55);
      border: 3px solid rgba(230,120,40,0.95); pointer-events: none;
      transition: all 0.6s ease-out; }
  `;
  const boot = () => {
    if (!document.getElementById('ew-tap-style')) {
      const st = document.createElement('style');
      st.id = 'ew-tap-style'; st.textContent = css;
      (document.head || document.documentElement).appendChild(st);
    }
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else { boot(); }
  const show = (x, y) => {
    boot();
    const r = document.createElement('div');
    r.className = 'ew-tap';
    r.style.left = x + 'px'; r.style.top = y + 'px';
    document.documentElement.appendChild(r);
    requestAnimationFrame(() => {
      r.style.width = '58px'; r.style.height = '58px';
      r.style.margin = '-29px 0 0 -29px'; r.style.opacity = '0';
    });
    setTimeout(() => r.remove(), 700);
  };
  addEventListener('touchstart', e => {
    const t = e.touches[0]; if (t) show(t.clientX, t.clientY);
  }, true);
  addEventListener('mousedown', e => show(e.clientX, e.clientY), true);
})();
"""


def _read_env_file(path: Path) -> dict:
    values: dict = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, rest = line.partition("=")
            values[key.strip()] = rest.strip().strip('"').strip("'")
    return values


def _run_ffmpeg(cmd: list) -> None:
    done = subprocess.run(cmd, capture_output=True, timeout=300)
    if done.returncode != 0:
        raise RuntimeError("ffmpeg failed for " + cmd[-1])


def to_y4m(source: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 0:
        return target
    if source.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
        cmd = ["ffmpeg", "-y", "-nostdin", "-protocol_whitelist", "file",
               "-loop", "1", "-i", str(source), "-t", "20",
               "-vf", "scale=800:600:force_original_aspect_ratio=decrease,"
                      "pad=800:600:(ow-iw)/2:(oh-ih)/2,fps=10",
               "-pix_fmt", "yuv420p", str(target)]
    else:
        cmd = ["ffmpeg", "-y", "-nostdin", "-protocol_whitelist", "file",
               "-stream_loop", "-1", "-i", str(source), "-t", "30",
               "-vf", "scale=800:600:force_original_aspect_ratio=decrease,"
                      "pad=800:600:(ow-iw)/2:(oh-ih)/2,fps=10",
               "-pix_fmt", "yuv420p", str(target)]
    _run_ffmpeg(cmd)
    return target


def blur_sharp_y4m(crash: Path, target: Path,
                   blur_s: int = 7, total_s: int = 40) -> Path:
    """One feed that starts out of focus and turns sharp.

    The change sits at blur_s. The first photo falls inside the
    blurred part and the retake lands in the sharp part. The
    blurred part carries a small brightness lift: the crash
    screen is black with white text, and without the lift the
    page's own darkness check stops the photo before the
    server's focus check can answer. The lift leaves the
    Laplacian variance where the blur put it.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 0:
        return target
    sharp_s = max(total_s - blur_s, 5)
    graph = (
        "[0:v]scale=800:600:force_original_aspect_ratio=decrease,"
        "pad=800:600:(ow-iw)/2:(oh-ih)/2,gblur=sigma=12,"
        "eq=brightness=0.13,fps=10[blur];"
        "[1:v]scale=800:600:force_original_aspect_ratio=decrease,"
        "pad=800:600:(ow-iw)/2:(oh-ih)/2,fps=10[sharp];"
        "[blur][sharp]concat=n=2:v=1:a=0,format=yuv420p")
    cmd = ["ffmpeg", "-y", "-nostdin", "-protocol_whitelist", "file",
           "-loop", "1", "-t", str(blur_s), "-i", str(crash),
           "-loop", "1", "-t", str(sharp_s), "-i", str(crash),
           "-filter_complex", graph, "-pix_fmt", "yuv420p", str(target)]
    _run_ffmpeg(cmd)
    return target


def _wait_status(page, item, needle, timeout_s: int) -> str:
    """Wait until an item's status line holds a needle. Best effort."""
    needles = (needle,) if isinstance(needle, str) else tuple(needle)
    status = item.locator("[data-status]")
    deadline = time.time() + timeout_s
    text = ""
    while time.time() < deadline:
        try:
            text = status.inner_text(timeout=2000).strip()
        except Exception:
            text = ""
        if any(n.lower() in text.lower() for n in needles):
            return text
        page.wait_for_timeout(1500)
    return text


def _tap(page, locator) -> None:
    locator.scroll_into_view_if_needed(timeout=8000)
    page.wait_for_timeout(350)
    box = locator.bounding_box(timeout=8000)
    if box:
        page.touchscreen.tap(box["x"] + box["width"] / 2,
                             box["y"] + box["height"] / 2)
    else:
        locator.tap(timeout=8000)
    page.wait_for_timeout(700)


def _launch(pw, out_dir: Path, y4m: Path | None, *, record: bool = True):
    chrome_args = ["--use-fake-ui-for-media-stream",
                   "--use-fake-device-for-media-stream"]
    if y4m is not None:
        chrome_args.append(f"--use-file-for-fake-video-capture={y4m}")
    browser = pw.chromium.launch(
        executable_path="/usr/bin/google-chrome", args=chrome_args)
    kwargs = dict(viewport={"width": 390, "height": 844}, is_mobile=True,
                  has_touch=True, device_scale_factor=2,
                  permissions=["camera", "microphone"])
    if record:
        kwargs.update(record_video_dir=str(out_dir),
                      record_video_size={"width": 390, "height": 844})
    context = browser.new_context(**kwargs)
    context.add_init_script(TAP_SCRIPT)
    return browser, context


def scene5(page, url: str) -> None:
    page.goto(url, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(1800)
    items = page.locator("[data-item-id]")
    # Step 1 of 3 shows first. Move to the photo step.
    _tap(page, page.locator("[data-nav-next]"))
    photo = items.nth(1)
    _tap(page, photo.locator("[data-start-camera]"))
    page.wait_for_timeout(3200)
    _tap(page, photo.locator("[data-take-photo]"))
    heard = _wait_status(page, photo, "Please retake", 120)
    print(f"record_phone: scene 5 first verdict: {heard[:90]}")
    # The feed is sharp by now. Open the camera again and retake.
    # A reopened fake camera may restart its file, so hold until
    # the preview has had time to reach the sharp part, and take
    # again where a retake answer comes back instead.
    for attempt in range(3):
        _tap(page, photo.locator("[data-start-camera]"))
        page.wait_for_timeout(7000)
        _tap(page, photo.locator("[data-take-photo]"))
        heard = _wait_status(page, photo, ("Got it", "Please retake"), 180)
        print(f"record_phone: scene 5 verdict {attempt + 2}: "
              f"{heard[:90]}")
        if "Got it" in heard:
            break
    page.wait_for_timeout(2200)


def scene6(page, url: str, blink: Path, demo_dir: Path) -> None:
    """Film the light, then send the clip the server can read.

    The live recording runs through its countdown and stops. A
    Chrome recorder file carries no duration, so the server
    asks for a retake; the same clip then goes through the
    page's video file button and the server reads it in full.
    Event times land in a sidecar for the edit.
    """
    started = time.time()
    marks: dict = {}
    page.goto(url, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(1800)
    items = page.locator("[data-item-id]")
    video = items.nth(0)
    # After the photo the stepper opens here. From step 2, go back.
    progress = page.locator("[data-progress]").inner_text(timeout=5000)
    if "Step 1" not in progress:
        _tap(page, page.locator("[data-nav-back]"))
    _tap(page, video.locator("[data-start-camera]"))
    page.wait_for_timeout(1500)
    _tap(page, video.locator("[data-record-video]"))
    marks["record_tap_s"] = round(time.time() - started, 1)
    # Countdown (3 s) plus the full recording (15 s), then upload.
    page.wait_for_timeout(20000)
    heard = _wait_status(page, video, "Please retake", 90)
    marks["retake_s"] = round(time.time() - started, 1)
    print(f"record_phone: scene 6 live verdict: {heard[:90]}")
    page.wait_for_timeout(1500)
    video.locator("[data-capture]").set_input_files(str(blink))
    heard = _wait_status(page, video, "Got it", 300)
    marks["got_it_s"] = round(time.time() - started, 1)
    print(f"record_phone: scene 6 file verdict: {heard[:90]}")
    page.wait_for_timeout(2200)
    sidecar = demo_dir / "phone" / "scene06-times.json"
    sidecar.write_text(json.dumps(marks), encoding="utf-8")


def finish(page, url: str) -> None:
    page.goto(url, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(1800)
    items = page.locator("[data-item-id]")
    question = items.nth(2)
    box = question.locator("[data-answer-text]")
    if box.count():
        box.first.fill("No, nothing changed.")
        page.wait_for_timeout(600)
        _tap(page, question.locator("[data-answer-save]"))
        page.wait_for_timeout(2000)
    _tap(page, page.locator(".ew-done"))
    page.wait_for_timeout(3000)
    msg = page.locator("[data-done-msg]").inner_text(timeout=5000)
    print(f"record_phone: finish: {msg[:90]}")


def wait_triage(demo_dir: Path, env_file: Path,
                timeout_s: int = 600) -> int:
    if not WG_BASE:
        print("record_phone: set EYEWITNESS_WG_URL for the triage wait")
        return 2
    ids = _read_env_file(env_file)
    ticket = ids.get("WEGWEISER_TICKET_ID", "")
    if not ticket:
        print("record_phone: WEGWEISER_TICKET_ID missing")
        return 2
    from playwright.sync_api import sync_playwright
    started = time.time()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path="/usr/bin/google-chrome", headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            storage_state=str(demo_dir / ".auth" / "wegweiser.json"))
        page = context.new_page()
        verdict = "Not read yet"
        while time.time() - started < timeout_s:
            page.goto(WG_BASE + "/tickets/" + ticket,
                      wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(2500)
            body = page.inner_text("body")
            if "Not read yet" not in body:
                verdict = "read"
                break
            page.wait_for_timeout(30000)
        context.close()
        browser.close()
    elapsed = int(time.time() - started)
    print(f"record_phone: triage after {elapsed}s: {verdict}")
    return 0 if verdict == "read" else 1


def record(url: str, scene: int, demo_dir: Path,
           crash: Path | None, fake_source: Path | None) -> Path:
    out_dir = demo_dir / "phone"
    out_dir.mkdir(parents=True, exist_ok=True)
    if scene == 5:
        if crash is None:
            raise SystemExit("scene 5 needs --crash <crash screen png>")
        y4m = blur_sharp_y4m(crash, out_dir / "fake-scene05-blur-sharp.y4m")
    else:
        if fake_source is None:
            raise SystemExit("scene 6 needs --fake-source <blink clip>")
        y4m = to_y4m(fake_source, out_dir / "fake-scene06.y4m")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser, context = _launch(pw, out_dir, y4m)
        page = context.new_page()
        if scene == 5:
            scene5(page, url)
        else:
            scene6(page, url, fake_source, demo_dir)
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
    else:
        webs = sorted(out_dir.glob("*.webm"),
                      key=lambda p: p.stat().st_mtime)
        if not webs:
            raise SystemExit("record_phone: no video file produced")
        webs[-1].replace(target)
    print(f"record_phone: wrote {target}")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Phone demo recordings")
    parser.add_argument("--url", default="",
                        help="customer page address")
    parser.add_argument("--scene", type=int, default=5, choices=(5, 6))
    parser.add_argument("--demo-dir",
                        default=os.environ.get("EYEWITNESS_DEMO_DIR", ""))
    parser.add_argument("--env-file",
                        default=str(Path.home() / ".config" /
                                    "eyewitness-demo" / "demo-ticket.env"))
    parser.add_argument("--crash", default="",
                        help="crash screen image for the scene 5 feed")
    parser.add_argument("--fake-source", default="",
                        help="synthetic clip for the scene 6 feed")
    parser.add_argument("--finish", action="store_true",
                        help="answer the last step and press Done")
    parser.add_argument("--wait-triage", action="store_true",
                        help="wait until the ticket triage has been read")
    args = parser.parse_args()
    if not args.demo_dir:
        print("EYEWITNESS_DEMO_DIR not set")
        return 2
    demo_dir = Path(args.demo_dir)
    if args.wait_triage:
        return wait_triage(demo_dir, Path(args.env_file))
    url = args.url
    if not url:
        url = _read_env_file(Path(args.env_file)).get("CUSTOMER_URL", "")
    if not url:
        print("no customer page address (--url or CUSTOMER_URL)")
        return 2
    if args.finish:
        out_dir = demo_dir / "phone"
        out_dir.mkdir(parents=True, exist_ok=True)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser, context = _launch(pw, out_dir, None, record=False)
            page = context.new_page()
            finish(page, url)
            context.close()
            browser.close()
        return 0
    record(url, args.scene, demo_dir,
           Path(args.crash) if args.crash else None,
           Path(args.fake_source) if args.fake_source else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

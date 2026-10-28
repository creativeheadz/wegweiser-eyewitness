#!/usr/bin/env python3
"""Desktop recordings for demo scenes 3, 4 and 10.

No passwords sit in this file and none are asked for here. A login
run opens a visible browser at an address given as an argument.
Andrei signs in in that window himself. The Playwright storage
state then goes to the demo folder under .auth with mode 600.
Recording runs headless with that saved state. Every address and
ticket number comes from arguments or environment variables.

Usage:
  venv/bin/python eyewitness/demo/record_desktop.py \
    --login wegweiser --url <sign-in address>
  venv/bin/python eyewitness/demo/record_desktop.py \
    --site wegweiser --scene 4 --base-url <app address> \
    --ticket <ticket number>
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _auth_path(demo_dir: Path, site: str) -> Path:
    return demo_dir / ".auth" / f"{site}.json"


def do_login(site: str, url: str, demo_dir: Path) -> int:
    from playwright.sync_api import sync_playwright
    target = _auth_path(demo_dir, site)
    # The saved state is a signed-in session, so its folder is private
    # before anything is written into it.
    target.parent.mkdir(parents=True, exist_ok=True)
    target.parent.chmod(0o700)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path="/usr/bin/google-chrome",
            headless=False)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        print(f"Sign in as yourself in the open window for {site}.")
        print("When the ticket list or home page shows, return here "
              "and press Enter.")
        try:
            input()
        except EOFError:
            page.wait_for_timeout(5000)
        context.storage_state(path=str(target))
        browser.close()
    try:
        target.chmod(0o600)
    except OSError:
        pass
    print(f"record_desktop: saved sign-in state for {site}")
    return 0


def do_record(site: str, scene: int, base_url: str, ticket: str,
              demo_dir: Path) -> int:
    auth = _auth_path(demo_dir, site)
    if not auth.exists():
        print(f"No saved sign-in for {site}. Run the login step first:")
        print(f"  record_desktop.py --login {site} --url <sign-in address>")
        return 2
    out_dir = demo_dir / "desktop"
    out_dir.mkdir(parents=True, exist_ok=True)
    address = base_url.rstrip("/") + f"/helpdesk/tickets/{ticket}"
    if site == "zammad":
        address = base_url.rstrip("/") + f"/#ticket/zoom/{ticket}"
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path="/usr/bin/google-chrome",
            headless=True)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            storage_state=str(auth),
            record_video_dir=str(out_dir),
            record_video_size={"width": 1920, "height": 1080})
        page = context.new_page()
        page.goto(address, wait_until="networkidle")
        page.wait_for_timeout(2500)
        page.mouse.wheel(0, 600)
        page.wait_for_timeout(2500)
        page.mouse.wheel(0, 600)
        page.wait_for_timeout(2500)
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
        print(f"record_desktop: wrote {target}")
        return 0
    webs = sorted(out_dir.glob("*.webm"), key=lambda p: p.stat().st_mtime)
    if webs:
        webs[-1].replace(target)
        print(f"record_desktop: wrote {target}")
        return 0
    print("record_desktop: no video file produced")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Desktop demo recordings")
    parser.add_argument("--login", default="",
                        help="site name to sign in for, then save state")
    parser.add_argument("--site", default="wegweiser")
    parser.add_argument("--scene", type=int, default=4)
    parser.add_argument("--url", default="",
                        help="sign-in address for the login step")
    parser.add_argument("--base-url",
                        default=os.environ.get("EYEWITNESS_APP_URL", ""),
                        help="app or desk address for recording")
    parser.add_argument("--ticket",
                        default=os.environ.get("EYEWITNESS_TICKET", ""),
                        help="ticket number or id")
    parser.add_argument("--demo-dir",
                        default=os.environ.get("EYEWITNESS_DEMO_DIR", ""))
    args = parser.parse_args()
    if not args.demo_dir:
        print("EYEWITNESS_DEMO_DIR not set")
        return 2
    demo_dir = Path(args.demo_dir)
    if args.login:
        if not args.url:
            print("--url is needed for the login step")
            return 2
        return do_login(args.login, args.url, demo_dir)
    if not args.base_url or not args.ticket:
        print("--base-url and --ticket are needed to record")
        return 2
    return do_record(args.site, args.scene, args.base_url,
                     args.ticket, demo_dir)


if __name__ == "__main__":
    raise SystemExit(main())

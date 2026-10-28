#!/usr/bin/env python3
"""Desktop recordings for demo scenes 3, 4 and 10.

Playwright with the saved sign-in states opens Zammad and the
Wegweiser ticket view in one page, so one take covers both sides
and the edit crossfades between them. The viewport is 1280 by 720
recorded at 1920 by 1080, so text reads large in the cut.

An injected cursor follows eased mouse paths and ripples on
clicks. Evidence links and QR codes are blurred by injected CSS
and a small guard script, the Zammad address banner and its
welcome tour are hidden, and no address, token or ticket id is
printed. Ticket ids come from the demo ticket env file.

Usage:
  venv/bin/python eyewitness/demo/record_desktop.py --scene 3
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

WG_BASE = os.environ.get("EYEWITNESS_WG_URL", "")
ZAMMAD_BASE = os.environ.get("EYEWITNESS_ZAMMAD_URL", "")

INIT_SCRIPT = r"""
(() => {
  const css = `
    a[href*="/eyewitness/"] { filter: blur(14px) !important; }
    img[alt*="QR"] { filter: blur(20px) !important; }
    .modal--clue, .modal-backdrop { display: none !important; }
    .ew-req > p > a { filter: blur(14px) !important; }
    #ew-cursor { position: fixed; z-index: 2147483647; width: 26px;
      height: 26px; margin: -4px 0 0 -4px; pointer-events: none;
      background: url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="26" height="26"><path d="M4 2 L4 20 L9 15.5 L12 22 L15 20.8 L12.2 14.5 L19 14.5 Z" fill="black" stroke="white" stroke-width="1.6"/></svg>') no-repeat; }
    .ew-ripple { position: fixed; z-index: 2147483646; width: 14px;
      height: 14px; margin: -7px 0 0 -7px; border-radius: 50%;
      border: 3px solid rgba(230,120,40,0.9); pointer-events: none;
      transition: all 0.55s ease-out; }
  `;
  const boot = () => {
    if (!document.getElementById('ew-style')) {
      const st = document.createElement('style');
      st.id = 'ew-style'; st.textContent = css;
      (document.head || document.documentElement).appendChild(st);
    }
    if (!document.getElementById('ew-cursor')) {
      const cur = document.createElement('div');
      cur.id = 'ew-cursor';
      document.documentElement.appendChild(cur);
      let cx = innerWidth / 2, cy = innerHeight / 2;
      cur.style.left = cx + 'px'; cur.style.top = cy + 'px';
      addEventListener('mousemove', e => {
        cx = e.clientX; cy = e.clientY;
        cur.style.left = cx + 'px'; cur.style.top = cy + 'px';
      }, true);
      addEventListener('mousedown', e => {
        const r = document.createElement('div');
        r.className = 'ew-ripple';
        r.style.left = e.clientX + 'px'; r.style.top = e.clientY + 'px';
        document.documentElement.appendChild(r);
        requestAnimationFrame(() => {
          r.style.width = '52px'; r.style.height = '52px';
          r.style.margin = '-26px 0 0 -26px'; r.style.opacity = '0';
        });
        setTimeout(() => r.remove(), 650);
      }, true);
    }
    // Hide the Zammad address banner and welcome tour, and blur
    // any evidence link the CSS missed (Zammad article bodies).
    document.querySelectorAll('body *').forEach(el => {
      if (el.children.length > 6) return;
      const t = (el.textContent || '');
      if (t.includes("isn't the address Zammad")) el.style.display = 'none';
      if (t.includes('/eyewitness/') && t.length < 400 &&
          el.tagName !== 'SCRIPT' && el.tagName !== 'STYLE') {
        el.style.filter = 'blur(14px)';
      }
    });
    document.querySelectorAll('.popover, .clue-popover, .modal--clue').forEach(
      el => { el.style.display = 'none'; });
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else { boot(); }
  setInterval(boot, 700);
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


def _ease(page, x1, y1, x2, y2, steps=36):
    import math
    for i in range(1, steps + 1):
        t = i / steps
        e = t * t * (3 - 2 * t)
        page.mouse.move(x1 + (x2 - x1) * e, y1 + (y2 - y1) * e)
        page.wait_for_timeout(16)


class Take:
    def __init__(self, page):
        self.page = page
        self.x, self.y = 640, 360

    def move_to(self, x, y):
        _ease(self.page, self.x, self.y, x, y)
        self.x, self.y = x, y

    def rest_on(self, locator, hold_ms=2200):
        """Scroll an element into view, rest the cursor on it."""
        try:
            locator.scroll_into_view_if_needed(timeout=6000)
        except Exception:
            pass
        self.page.wait_for_timeout(450)
        try:
            box = locator.bounding_box(timeout=4000)
        except Exception:
            box = None
        if box:
            self.move_to(box["x"] + box["width"] / 2,
                         box["y"] + box["height"] / 2)
        self.page.wait_for_timeout(hold_ms)

    def smooth_scroll(self, dy=520):
        steps = 24
        for _ in range(steps):
            self.page.mouse.wheel(0, dy / steps)
            self.page.wait_for_timeout(28)

    def click_at(self, x, y):
        self.move_to(x, y)
        self.page.wait_for_timeout(350)
        self.page.mouse.click(x, y)


def _combined_context(browser, demo_dir: Path):
    wg_state = demo_dir / ".auth" / "wegweiser.json"
    zm_state = demo_dir / ".auth" / "zammad.json"
    context = browser.new_context(
        viewport={"width": 1280, "height": 720},
        device_scale_factor=1.5,
        storage_state=str(wg_state),
        record_video_dir=str(demo_dir / "desktop-raw"),
        record_video_size={"width": 1920, "height": 1080})
    if zm_state.exists():
        zammad = json.loads(zm_state.read_text(encoding="utf-8"))
        cookies = zammad.get("cookies") or []
        if cookies:
            context.add_cookies(cookies)
    context.add_init_script(INIT_SCRIPT)
    return context


def _zammad_hide_tour(page):
    page.evaluate("""() => {
      document.querySelectorAll('.popover, .clue-popover, .modal--clue').forEach(
        e => e.remove());
      document.querySelectorAll('body *').forEach(el => {
        if (el.children.length <= 6 &&
            (el.textContent || '').includes("isn't the address Zammad")) {
          el.style.display = 'none';
        }
      });
    }""")


def scene3(page, ids) -> None:
    take = Take(page)
    page.goto(ZAMMAD_BASE + "/", wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3000)
    _zammad_hide_tour(page)
    # The overview list: click the Overviews entry in the left nav.
    clicked = page.evaluate("""() => {
      const links = Array.from(document.querySelectorAll('a, li, div'));
      const hit = links.find(e => (e.textContent || '').trim() === 'Overviews');
      if (hit) { hit.click(); return true; } return false; }""")
    page.wait_for_timeout(3500)
    _zammad_hide_tour(page)
    if not clicked:
        page.goto(ZAMMAD_BASE + "/#ticket/view", wait_until="networkidle",
                  timeout=60000)
        page.wait_for_timeout(3000)
    # The demo ticket has no owner, so it sits with the open tickets.
    page.evaluate("""() => {
      const links = Array.from(document.querySelectorAll('a, li, div'));
      const hit = links.find(e => {
        const t = (e.textContent || '').trim();
        return t.startsWith('Open Tickets') && t.length < 24; });
      if (hit) hit.click(); }""")
    page.wait_for_timeout(3500)
    _zammad_hide_tour(page)
    row = page.get_by_text("Black screen, now it just blinks").last
    take.rest_on(row, hold_ms=1600)
    try:
        box = row.bounding_box(timeout=4000)
        if box:
            take.click_at(box["x"] + box["width"] / 2,
                          box["y"] + box["height"] / 2)
    except Exception:
        page.goto(ZAMMAD_BASE + "/#ticket/zoom/" + ids["ZAMMAD_TICKET_ID"],
                  wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(4500)
    _zammad_hide_tour(page)
    # Hold on Brenda's message at the top of the thread.
    msg = page.get_by_text("my computer went to a black screen").first
    take.rest_on(msg, hold_ms=3400)
    # Wegweiser: the top of the ticket view.
    page.goto(WG_BASE + "/tickets/" + ids["WEGWEISER_TICKET_ID"],
              wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3500)
    take.rest_on(page.get_by_text("RECEPTION-PC").first, hold_ms=2400)
    take.rest_on(page.get_by_text("Not read yet").first, hold_ms=3400)


def scene4(page, ids) -> None:
    take = Take(page)
    page.goto(WG_BASE + "/tickets/" + ids["WEGWEISER_TICKET_ID"],
              wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3500)
    panel = page.locator("[data-ew-panel]")
    take.rest_on(panel.locator("text=Missing from the ticket").first,
                 hold_ms=2200)
    take.rest_on(panel.locator("text=Physical symptom").first, hold_ms=2200)
    items = panel.locator(".ew-asked li")
    for i in range(min(items.count(), 3)):
        take.rest_on(items.nth(i), hold_ms=2000)
    # Zammad: the customer-visible note with items A, B and C.
    page.goto(ZAMMAD_BASE + "/#ticket/zoom/" + ids["ZAMMAD_TICKET_ID"],
              wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(4500)
    _zammad_hide_tour(page)
    note = page.get_by_text("To progress your ticket").first
    take.rest_on(note, hold_ms=2200)
    take.rest_on(page.get_by_text("A. Film the power light").first,
                 hold_ms=2000)
    take.rest_on(page.get_by_text("B. Photograph the error").first,
                 hold_ms=2000)
    take.rest_on(page.get_by_text("C. Did anything change").first,
                 hold_ms=2600)


def scene10(page, ids) -> None:
    take = Take(page)
    page.goto(ZAMMAD_BASE + "/#ticket/zoom/" + ids["ZAMMAD_TICKET_ID"],
              wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(4500)
    _zammad_hide_tour(page)
    note = page.get_by_text("Eyewitness - evidence for this ticket").first
    take.rest_on(note, hold_ms=2600)
    # Rest on the reading lines under the note, one by one.
    page.wait_for_timeout(600)
    take.smooth_scroll(360)
    page.wait_for_timeout(2400)
    take.smooth_scroll(360)
    page.wait_for_timeout(2400)
    # Wegweiser: the governor's brief, then the received evidence.
    page.goto(WG_BASE + "/tickets/" + ids["WEGWEISER_TICKET_ID"],
              wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3500)
    triage = page.locator("text=Triage").first
    take.rest_on(triage, hold_ms=2000)
    panel = page.locator("[data-ew-panel]")
    items = panel.locator(".ew-asked li")
    for i in range(min(items.count(), 3)):
        take.rest_on(items.nth(i), hold_ms=2400)
    page.wait_for_timeout(1200)


SCENES = {3: scene3, 4: scene4, 10: scene10}


def record(scene: int, demo_dir: Path, env_file: Path) -> Path:
    if not WG_BASE or not ZAMMAD_BASE:
        raise SystemExit("set EYEWITNESS_WG_URL and EYEWITNESS_ZAMMAD_URL "
                         "or pass --wg-base and --zammad-base")
    ids = _read_env_file(env_file)
    for key in ("ZAMMAD_TICKET_ID", "WEGWEISER_TICKET_ID"):
        if not ids.get(key):
            raise SystemExit(f"{key} missing in the demo ticket env file")
    raw_dir = demo_dir / "desktop-raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_dir = demo_dir / "desktop"
    out_dir.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path="/usr/bin/google-chrome", headless=True)
        context = _combined_context(browser, demo_dir)
        page = context.new_page()
        SCENES[scene](page, ids)
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
        webs = sorted(raw_dir.glob("*.webm"),
                      key=lambda p: p.stat().st_mtime)
        if not webs:
            raise SystemExit("record_desktop: no video file produced")
        webs[-1].replace(target)
    print(f"record_desktop: wrote {target}")
    return target


def do_login(site: str, url: str, demo_dir: Path) -> int:
    from playwright.sync_api import sync_playwright
    target = demo_dir / ".auth" / f"{site}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.parent.chmod(0o700)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path="/usr/bin/google-chrome", headless=False)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Desktop demo recordings")
    parser.add_argument("--login", default="",
                        help="site name to sign in for, then save state")
    parser.add_argument("--url", default="",
                        help="sign-in address for the login step")
    parser.add_argument("--scene", type=int, default=3, choices=(3, 4, 10))
    parser.add_argument("--wg-base", default="",
                        help="Wegweiser base address")
    parser.add_argument("--zammad-base", default="",
                        help="Zammad base address")
    parser.add_argument("--demo-dir",
                        default=os.environ.get("EYEWITNESS_DEMO_DIR", ""))
    parser.add_argument("--env-file",
                        default=str(Path.home() / ".config" /
                                    "eyewitness-demo" / "demo-ticket.env"))
    args = parser.parse_args()
    if not args.demo_dir:
        print("EYEWITNESS_DEMO_DIR not set")
        return 2
    demo_dir = Path(args.demo_dir)
    global WG_BASE, ZAMMAD_BASE
    if args.wg_base:
        WG_BASE = args.wg_base.rstrip("/")
    if args.zammad_base:
        ZAMMAD_BASE = args.zammad_base.rstrip("/")
    if args.login:
        if not args.url:
            print("--url is needed for the login step")
            return 2
        return do_login(args.login, args.url, demo_dir)
    record(args.scene, demo_dir, Path(args.env_file))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

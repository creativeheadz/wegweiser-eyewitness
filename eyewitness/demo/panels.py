#!/usr/bin/env python3
"""Muse Image panels for the demo scenes.

Reads panels.yaml beside this file and writes one PNG per panel into
the demo folder under panels/. A panel that already sits in the demo
folder is kept, so a rebuild spends nothing. Each new generation
costs one cent through the Eyewitness Meta key. No text goes inside
any picture; captions are added in the edit.

Usage:
  EYEWITNESS_DEMO_DIR=/path/to/demo-media venv/bin/python \
    eyewitness/demo/panels.py [--force]
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _load_panels() -> dict:
    import yaml
    path = Path(__file__).with_name("panels.yaml")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def generate(demo_dir: Path, *, force: bool = False) -> list[Path]:
    spec = _load_panels()
    style = spec.get("style") or ""
    out_dir = demo_dir / "panels"
    out_dir.mkdir(parents=True, exist_ok=True)
    key = (os.environ.get("EYEWITNESS_META_API_KEY") or "").strip()
    made: list[Path] = []
    for panel in spec.get("panels") or []:
        target = out_dir / panel["file"]
        made.append(target)
        if target.exists() and not force:
            print(f"panels: keep {target.name}")
            continue
        if not key:
            print(f"panels: no Meta key, skip {target.name}")
            continue
        prompt = f"{panel['prompt']}. {style}."
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url="https://api.meta.ai/v1")
        resp = client.images.generate(model="muse-image-1.0",
                                      prompt=prompt, n=1,
                                      size="1024x1024")
        data = (resp.data or [None])[0]
        blob = None
        b64 = getattr(data, "b64_json", None) if data else None
        if b64:
            blob = base64.b64decode(b64)
        else:
            url = getattr(data, "url", None) if data else None
            if url:
                import requests
                got = requests.get(url, timeout=60)
                got.raise_for_status()
                blob = got.content
        if not blob:
            print(f"panels: no image for {target.name}")
            continue
        target.write_bytes(blob)
        print(f"panels: wrote {target.name} ($0.01)")
    return made


def main() -> int:
    parser = argparse.ArgumentParser(description="Demo comic panels")
    parser.add_argument("--demo-dir",
                        default=os.environ.get("EYEWITNESS_DEMO_DIR", ""))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not args.demo_dir:
        print("EYEWITNESS_DEMO_DIR not set")
        return 2
    generate(Path(args.demo_dir), force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

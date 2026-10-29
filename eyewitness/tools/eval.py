#!/usr/bin/env python3
"""Reader evaluation over the golden set and the synthetic set.

Run each reader over two sets and report accuracy per reader with
the cost per ticket. The golden set holds captures from physical
machines. The synthetic set holds files made on this machine, each
marked synthetic in its manifest. Results for the two sets stay
apart in the report.

The Meta client cache is used, so a second run over the same files
costs nothing. The report goes to eyewitness/docs/eval.md unless a
path is given.

Usage:
  EYEWITNESS_GOLDEN_DIR=/path/to/golden \
  EYEWITNESS_SYNTHETIC_DIR=/path/to/synthetic \
  EYEWITNESS_DEMO_DIR=/path/to/demo-media \
  venv/bin/python eyewitness/tools/eval.py [--out path]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

READER_FOR_KIND = {
    "screen_photo": "screen",
    "power_light_video": "video",
    "voice_note": "voice",
    "photo": "photo",
    "sticker": "sticker",
    "printer_panel": "vision",
    "printer_test_page": "vision",
    "display_fault": "vision",
    "network_lights": "vision",
    "beep_recording": "beeps",
}


def _load_manifest(folder: Path) -> list[dict]:
    path = folder / "manifest.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items") or []
    out = []
    for item in items:
        entry = dict(item)
        entry["_folder"] = str(folder)
        entry["_path"] = str(folder / item["file"])
        out.append(entry)
    return out


def _file_cache(demo_dir: Path):
    cache_path = demo_dir / "eval-cache.json"
    store: dict = {}
    if cache_path.exists():
        try:
            store = json.loads(cache_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            store = {}

    def get(key):
        if key not in store:
            return None
        value = store[key]
        if isinstance(value, dict) and "__str__" in value:
            return value["__str__"]
        return value

    def put(key, value):
        if isinstance(value, str):
            store[key] = {"__str__": value}
        else:
            store[key] = value
        try:
            demo_dir.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(store), encoding="utf-8")
        except OSError:
            pass

    return get, put


def _make_client(demo_dir: Path, calls: list):
    from app.eyewitness.meta.client import MetaClient
    get, put = _file_cache(demo_dir)

    def log(row):
        calls.append(dict(row))

    return MetaClient(cache_get=get, cache_put=put, log_call=log,
                      spent_today=lambda: sum(c["usd"] for c in calls))


def _check(item: dict, result: dict) -> tuple[bool, list[str]]:
    expected = item.get("expected") or {}
    reading = result.get("reading") or {}
    notes: list[str] = []
    ok = True

    def fail(msg):
        nonlocal ok
        ok = False
        notes.append(msg)

    if "verdict" in expected and result.get("verdict") != expected["verdict"]:
        fail(f"verdict {result.get('verdict')} (wanted {expected['verdict']})")
    if "retake_reason" in expected:
        reason = (result.get("retake") or {}).get("reason")
        if reason != expected["retake_reason"]:
            fail(f"retake reason {reason} (wanted {expected['retake_reason']})")
    if "stop_code" in expected:
        got = str(reading.get("stop_code") or "")
        if expected["stop_code"].lower() not in got.lower():
            fail(f"stop code {got or 'none'} (wanted {expected['stop_code']})")
    if "error_codes" in expected:
        got_codes = [str(c) for c in (reading.get("error_codes") or [])]
        for want in expected["error_codes"]:
            if not any(want.lower() in c.lower() for c in got_codes):
                fail(f"error code {want} missing from {got_codes}")
    if "screen_found" in expected:
        if bool(reading.get("screen_found")) != bool(expected["screen_found"]):
            fail(f"screen_found {reading.get('screen_found')}")
    if "kind" in expected:
        if reading.get("kind") != expected["kind"]:
            fail(f"kind {reading.get('kind')} (wanted {expected['kind']})")
    if "legible" in expected:
        if bool(reading.get("legible")) != bool(expected["legible"]):
            fail(f"legible {reading.get('legible')}")
    if "pattern" in expected:
        if reading.get("pattern") != expected["pattern"]:
            fail(f"pattern {reading.get('pattern')} (wanted {expected['pattern']})")
    if "injection_flag" in expected:
        if bool(result.get("injection_flag")) != bool(expected["injection_flag"]):
            fail(f"injection flag {result.get('injection_flag')}")
    if expected.get("transcript_nonempty"):
        transcript = str(reading.get("transcript") or "")
        if len(transcript.strip()) < 3:
            fail("transcript empty")
    fields_want = expected.get("fields") or {}
    got_fields = reading.get("fields") or {}
    for name, want in fields_want.items():
        if name.endswith("_contains"):
            base = name[: -len("_contains")]
            got = got_fields.get(base)
            if isinstance(want, list):
                for member in want:
                    if member not in (got or []):
                        fail(f"field {base} missing {member} in {got}")
            elif str(want).lower() not in str(got or "").lower():
                fail(f"field {base} {got or 'none'} (wanted text {want})")
            continue
        got = got_fields.get(name)
        if isinstance(got, list) and not isinstance(want, list):
            if want not in got:
                fail(f"field {name} missing {want} in {got}")
        elif got != want:
            fail(f"field {name} {got} (wanted {want})")
    return ok, notes


def _run_one(item: dict, client) -> dict:
    kind = item.get("kind") or ""
    reader = READER_FOR_KIND.get(kind, "screen")
    path = Path(item["_path"])
    if not path.exists():
        return {"file": item["file"], "kind": kind, "reader": reader,
                "ok": False, "notes": ["file missing"], "verdict": "missing",
                "reading": {}, "injection_flag": False, "usd": 0.0}
    data = path.read_bytes()
    before = []
    # Cost for this item is measured by the calls logged while it runs.
    # The caller passes a shared list; the slice is taken by the caller
    # through a marker. Here we run and return the raw result.
    if reader == "vision":
        from app.eyewitness import catalogue as _cat
        from app.eyewitness.readers.vision import read_vision
        result = read_vision(data, _cat.get(kind) or {}, client=client)
    elif reader == "beeps":
        from app.eyewitness import catalogue as _cat
        from app.eyewitness.readers.beeps import read_beeps
        suffix = path.suffix.lower()
        mime = {".webm": "audio/webm", ".m4a": "audio/mp4",
                ".wav": "audio/wav"}.get(suffix, "audio/webm")
        result = read_beeps(data, mime, _cat.get(kind) or {})
    elif reader == "screen":
        from app.eyewitness.readers.screen import read_screen
        result = read_screen(data, client=client)
    elif reader == "video":
        from app.eyewitness.readers.video_lights import read_video
        suffix = path.suffix.lower()
        mime = {".mp4": "video/mp4", ".webm": "video/webm"}.get(
            suffix, "video/mp4")
        result = read_video(data, mime, client=client)
    elif reader == "voice":
        from app.eyewitness.readers.voice import read_voice
        suffix = path.suffix.lower()
        mime = {".webm": "audio/webm", ".m4a": "audio/mp4",
                ".wav": "audio/wav"}.get(suffix, "audio/webm")
        result = read_voice(data, mime, client=client)
    elif reader == "sticker":
        from app.eyewitness.readers.sticker import read_sticker
        result = read_sticker(data, client=client)
    else:
        from app.eyewitness.readers.quality import read_photo
        result = read_photo(data)
    ok, notes = _check(item, result)
    return {"file": item["file"], "kind": kind, "reader": reader,
            "ok": ok, "notes": notes, "verdict": result.get("verdict"),
            "reading": result.get("reading") or {},
            "injection_flag": bool(result.get("injection_flag")),
            "retake": result.get("retake")}


def _intake_cost(client, calls: list) -> float:
    """One intake read on the demo ticket text. Returns its cost."""
    from app.eyewitness.intake import INTAKE_SCHEMA, _load_intake_prompt, catalogue_text
    marker = len(calls)
    text = (
        "Origin: desk\nDevice: unknown\n\n"
        "Ticket text (untrusted data from the customer):\n"
        "<<<\nBlack screen, now it just blinks. "
        "PC keeps crashing with a blue screen.\n>>>\n\n"
        + catalogue_text())
    try:
        client.spark_json(system=_load_intake_prompt(),
                          user_content=[{"type": "text", "text": text}],
                          schema=INTAKE_SCHEMA, kind="intake")
    except Exception:
        return 0.0
    return sum(c["usd"] for c in calls[marker:])


def _fmt_usd(value: float) -> str:
    return f"${value:.4f}"


def _reader_table(rows: list[dict]) -> str:
    by_reader: dict[str, list[dict]] = {}
    for row in rows:
        by_reader.setdefault(row["reader"], []).append(row)
    lines = ["| Reader | Items | Passed | Cost |",
             "|---|---|---|---|"]
    for reader in sorted(by_reader):
        group = by_reader[reader]
        passed = sum(1 for r in group if r["ok"])
        cost = sum(r.get("usd", 0.0) for r in group)
        lines.append(f"| {reader} | {len(group)} | {passed} | {_fmt_usd(cost)} |")
    return "\n".join(lines)


def _detail_table(rows: list[dict]) -> str:
    lines = ["| File | Reader | Result | Detail |",
             "|---|---|---|---|"]
    for row in rows:
        reading = row.get("reading") or {}
        if row["reader"] == "screen":
            detail = str(reading.get("stop_code") or reading.get("summary") or "")
            if row.get("injection_flag"):
                detail += " (flagged)"
        elif row["reader"] == "video":
            detail = str(reading.get("pattern") or (row.get("retake") or {}).get("reason") or "")
        elif row["reader"] == "voice":
            transcript = str(reading.get("transcript") or "")[:60]
            detail = transcript.replace("|", " ")
        else:
            detail = str(reading.get("summary") or row.get("verdict") or "")
        detail = detail.replace("\n", " ")[:90]
        result = "pass" if row["ok"] else "fail"
        if row.get("notes"):
            detail += " - " + "; ".join(row["notes"])[:80]
        lines.append(f"| {row['file']} | {row['reader']} | {result} | {detail} |")
    return "\n".join(lines)


def evaluate(golden_dir: Path, synthetic_dir: Path,
             demo_dir: Path) -> dict:
    calls: list = []
    client = _make_client(demo_dir, calls)
    golden = _load_manifest(golden_dir) if golden_dir else []
    synthetic = _load_manifest(synthetic_dir) if synthetic_dir else []

    def run_set(items):
        rows = []
        for item in items:
            marker = len(calls)
            row = _run_one(item, client)
            row["usd"] = sum(c["usd"] for c in calls[marker:])
            row["cache_hits"] = sum(1 for c in calls[marker:] if c.get("cache_hit"))
            rows.append(row)
        return rows

    golden_rows = run_set(golden)
    synthetic_rows = run_set(synthetic)
    intake_usd = _intake_cost(client, calls)
    return {"golden": golden_rows, "synthetic": synthetic_rows,
            "calls": calls, "intake_usd": intake_usd,
            "configured": client.is_configured}


def render_report(result: dict, golden_dir: str, synthetic_dir: str) -> str:
    golden = result["golden"]
    synthetic = result["synthetic"]
    calls = result["calls"]
    total_usd = sum(c["usd"] for c in calls)
    cached = sum(1 for c in calls if c.get("cache_hit"))

    def set_cost(rows):
        return sum(r.get("usd", 0.0) for r in rows)

    # Cost for one ticket with a screen photo, a light video and a
    # voice note, plus the intake read. Each part uses the first
    # measured cost for that reader in either set.
    def first_cost(rows, reader):
        for row in rows:
            if row["reader"] == reader:
                return row.get("usd", 0.0)
        return 0.0

    all_rows = golden + synthetic
    screen_cost = first_cost(all_rows, "screen")
    video_cost = first_cost(all_rows, "video")
    voice_cost = first_cost(all_rows, "voice")
    ticket_cost = result["intake_usd"] + screen_cost + video_cost + voice_cost

    lines = []
    lines.append("# Eyewitness reader evaluation")
    lines.append("")
    import datetime
    lines.append(f"Date: {datetime.date.today().isoformat()}. The golden "
                 "set holds real captures: screens and devices "
                 "photographed or recorded from machines, physical or "
                 "virtual. The synthetic set holds files made on this "
                 "machine. The two sets stay apart in every table below.")
    lines.append("")
    lines.append("Folders come from EYEWITNESS_GOLDEN_DIR and "
                 "EYEWITNESS_SYNTHETIC_DIR. Paths stay out of this "
                 "file, which is published.")
    lines.append("")
    if not result["configured"]:
        lines.append("No Meta key was set for this run. Paid steps were "
                     "skipped and the readings below are the fallback "
                     "results. Set EYEWITNESS_META_API_KEY and run the "
                     "tool again for live readings.")
        lines.append("")
    lines.append("## Golden set (real captures)")
    lines.append("")
    if golden:
        lines.append(_reader_table(golden))
        lines.append("")
        lines.append(_detail_table(golden))
    else:
        lines.append("No items were found in the golden folder.")
    lines.append("")
    lines.append(f"Golden cost for this run: {_fmt_usd(set_cost(golden))} "
                 f"across {len(golden)} items.")
    lines.append("")
    lines.append("## Synthetic set (files made on this machine)")
    lines.append("")
    if synthetic:
        lines.append(_reader_table(synthetic))
        lines.append("")
        lines.append(_detail_table(synthetic))
    else:
        lines.append("No items were found in the synthetic folder.")
    lines.append("")
    lines.append(f"Synthetic cost for this run: {_fmt_usd(set_cost(synthetic))} "
                 f"across {len(synthetic)} items.")
    lines.append("")
    lines.append("## Cost for one ticket")
    lines.append("")
    lines.append("One ticket here means the intake read plus one screen "
                 "photo, one light video and one voice note. The parts "
                 "below use the first measured cost for each reader. "
                 "A repeat run reads the cache and costs nothing.")
    lines.append("")
    lines.append("| Part | Cost |")
    lines.append("|---|---|")
    lines.append(f"| Intake read | {_fmt_usd(result['intake_usd'])} |")
    lines.append(f"| Screen photo | {_fmt_usd(screen_cost)} |")
    lines.append(f"| Light video | {_fmt_usd(video_cost)} |")
    lines.append(f"| Voice note | {_fmt_usd(voice_cost)} |")
    lines.append(f"| One ticket in total | {_fmt_usd(ticket_cost)} |")
    lines.append("")
    lines.append(f"This run logged {len(calls)} Meta calls "
                 f"({cached} from the cache) for "
                 f"{_fmt_usd(total_usd)} in total. The cache file sits "
                 "in the demo folder and a second run over the same "
                 "files adds no cost.")
    lines.append("")
    lines.append("## How to read this")
    lines.append("")
    lines.append("Each reader returns a verdict in code. The screen "
                 "reader must name the stop code on the crash screen. "
                 "The video reader must count two amber flashes and "
                 "three white flashes on the blink clips, and must "
                 "return the light not found reason on the shaken clip "
                 "with no light. The voice reader must return a "
                 "transcript. The sticky note photo must set the flag "
                 "for text that looks like instructions to an AI.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Eyewitness reader evaluation")
    parser.add_argument("--golden-dir", default=os.environ.get("EYEWITNESS_GOLDEN_DIR", ""))
    parser.add_argument("--synthetic-dir", default=os.environ.get("EYEWITNESS_SYNTHETIC_DIR", ""))
    parser.add_argument("--demo-dir", default=os.environ.get("EYEWITNESS_DEMO_DIR", ""))
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    golden_dir = Path(args.golden_dir) if args.golden_dir else None
    synthetic_dir = Path(args.synthetic_dir) if args.synthetic_dir else None
    demo_dir = Path(args.demo_dir) if args.demo_dir else Path("/tmp/eyewitness-demo")
    demo_dir.mkdir(parents=True, exist_ok=True)

    if golden_dir is None or not golden_dir.is_dir():
        print("EYEWITNESS_GOLDEN_DIR not set or missing; nothing to evaluate")
        return 2
    if synthetic_dir is not None and not synthetic_dir.is_dir():
        print("synthetic folder missing; nothing to evaluate")
        return 2

    result = evaluate(golden_dir, synthetic_dir or Path(""), demo_dir)
    report = render_report(result, str(golden_dir), str(synthetic_dir or ""))
    out = Path(args.out) if args.out else Path(__file__).resolve().parents[1] / "docs" / "eval.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    golden_pass = sum(1 for r in result["golden"] if r["ok"])
    synth_pass = sum(1 for r in result["synthetic"] if r["ok"])
    total = sum(c["usd"] for c in result["calls"])
    print(f"golden: {golden_pass}/{len(result['golden'])} passed")
    print(f"synthetic: {synth_pass}/{len(result['synthetic'])} passed")
    print(f"calls: {len(result['calls'])} total ${_format(total)}")
    print(f"report: {out}")
    return 0


def _format(value: float) -> str:
    return f"{value:.4f}"


if __name__ == "__main__":
    raise SystemExit(main())

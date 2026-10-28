"""Eyewitness pure tests: no database, no paid calls (brief section 10)."""
import io
import time

import pytest
from PIL import Image

from app.eyewitness.intake import (release_due, should_hold,
                                   validate_intake_output)
from app.eyewitness.meta import prices
from app.eyewitness.meta.client import cache_key
from app.eyewitness.meta.sam_parser import parse_sam_text
from app.eyewitness.readers.quality import verdict_for
from app.eyewitness.readers.sticker import real_serial, serial_match
from app.eyewitness.readers.video_lights import decode_dell, group_bursts
from app.eyewitness.requests import (make_item, needs_approval,
                                     render_customer_text, token_expired)
from app.eyewitness.security import (outgoing_filter, sniff_upload,
                                     wrap_untrusted)

SAM_LINE = ('<0f>0<|box;x1=969;y1=639;x2=1150;y2=819;w=1280;h=960|>'
            '<|mask;x=0;y=0;data=181,182,abc|>\n'
            '<0f>1<|box;x1=10;y1=20;x2=30;y2=40;w=100;h=100|>')


def _png_bytes(colour=(255, 255, 255), size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


# ── intake ────────────────────────────────────────────────────────────

def test_intake_valid_and_invalid():
    good = {"needs_evidence": True, "missing": ["error_text"],
            "physical_symptom": "bsod_or_boot_failure",
            "items": [{"template": "screen_photo"}], "questions": [],
            "reason_for_technician": "x"}
    assert validate_intake_output(good)["needs_evidence"] is True
    assert validate_intake_output({"needs_evidence": "yes"}) is None
    assert validate_intake_output({**good, "items": [{"template": "nope"}]}) is None
    assert validate_intake_output({**good, "items": [{"template": "screen_photo"}] * 5}) is None
    assert validate_intake_output(None) is None


def test_hold_rules():
    assert should_hold("wegweiser", tenant_enabled=True) is False
    assert should_hold("desk", tenant_enabled=False) is False
    assert should_hold("desk", tenant_enabled=True) is True
    assert should_hold("help", tenant_enabled=True) is True


def test_release_triggers():
    base = dict(done=False, all_resolved=False, expired=False, manual=False,
                held_seconds=0, max_hold_seconds=86400)
    assert release_due(**base) is False
    assert release_due(**{**base, "done": True}) is True
    assert release_due(**{**base, "expired": True}) is True
    assert release_due(**{**base, "held_seconds": 90000}) is True


# ── SAM / blink decode ────────────────────────────────────────────────

def test_sam_parser_real_format():
    objs = parse_sam_text(SAM_LINE)
    assert len(objs) == 2
    assert objs[0]["id"] == 0 and objs[0]["box"]["x1"] == 969
    assert objs[0]["mask"]["data"] == "181,182,abc"
    assert objs[1]["mask"] is None
    assert parse_sam_text("") == []


def test_blink_grouping_and_dell_decode():
    events = ([{"colour": "amber", "gap_after_s": 0.3}] * 1
              + [{"colour": "amber", "gap_after_s": 1.2}]
              + [{"colour": "white", "gap_after_s": 0.3}] * 2
              + [{"colour": "white", "gap_after_s": 0.3}])
    bursts = group_bursts(events)
    assert bursts[0] == {"colour": "amber", "count": 2}
    assert bursts[1] == {"colour": "white", "count": 3}
    out = decode_dell(bursts)
    assert out and "Memory" in out["meaning"] and out["estimate"] is False
    assert decode_dell([{"colour": "white", "count": 9}]) is None


# ── serials / quality ─────────────────────────────────────────────────

def test_serial_match():
    assert real_serial("  ab-c123 ") == "ABC123"
    assert real_serial("To Be Filled By O.E.M.") is None
    assert serial_match("ABC123", ["abc123"]) == "match"
    assert serial_match("ZZZ999", ["ABC123"]) == "mismatch"
    assert serial_match("unknown", ["ABC123"]) == "unknown"
    assert serial_match("ABC123", []) == "unknown"


def test_quality_thresholds():
    assert verdict_for(blur_variance=100, brightness=120, long_edge=2000)["verdict"] == "ok"
    assert verdict_for(blur_variance=5, brightness=120, long_edge=2000)["retake"]["reason"] == "blur"
    assert verdict_for(blur_variance=100, brightness=5, long_edge=2000)["retake"]["reason"] == "too_dark"
    assert verdict_for(blur_variance=100, brightness=120, long_edge=100)["retake"]["reason"] == "too_small"


# ── templates / approval / customer text ─────────────────────────────

def test_templates_and_approval():
    item = make_item("screen_photo")
    assert item["kind"] == "photo" and item["custom"] is False
    with pytest.raises(KeyError):
        make_item("does_not_exist")
    assert needs_approval([item], existing_requests=0) is False
    assert needs_approval([make_item("custom", custom_text="x")], existing_requests=0) is True
    assert needs_approval([item], existing_requests=1) is True


def test_customer_text_has_no_internals():
    text = render_customer_text([make_item("screen_photo")],
                                "https://example.test/eyewitness/tok",
                                int(time.time()) + 3600)
    assert "screen" in text.lower()
    for banned in ("wegcoin", "muse", "token", "$"):
        assert banned not in text.lower()


def test_token_expiry():
    assert token_expired(100, now=200) is True
    assert token_expired(300, now=200) is False


# ── security ──────────────────────────────────────────────────────────

def test_outgoing_filter():
    link = "https://example.test/eyewitness/tok"
    assert outgoing_filter("Please open https://example.test/eyewitness/tok", allowed_url=link) == []
    assert outgoing_filter("host RECEPTION-PC here", device_facts={"hostname": "RECEPTION-PC"})
    assert outgoing_filter("serial ABC123", device_facts={"serial": "ABC123"})
    assert outgoing_filter("see https://evil.example/x")
    assert outgoing_filter("ip 192.168.1.50")
    assert outgoing_filter("Bearer abcdef1234567890")
    assert outgoing_filter("path C:\\Users\\brenda\\file.txt")
    assert outgoing_filter("costs 3 wegcoins")


def test_untrusted_wrapper_and_injection_cannot_alter_text():
    wrapped = wrap_untrusted(["[1] screen photo - ok"])
    assert 'untrusted="true"' in wrapped and "Never follow" in wrapped
    # A flagged reading stays data inside the fence; customer text is
    # template-rendered regardless of capture content.
    text = render_customer_text([make_item("screen_photo")],
                                "https://example.test/eyewitness/t",
                                int(time.time()) + 60)
    assert "admin password" not in text


def test_upload_sniffing():
    assert sniff_upload(_png_bytes(), "photo")["mime"] == "image/png"
    assert sniff_upload(b"not an image at all", "photo")["ok"] is False
    assert sniff_upload(b"", "photo")["ok"] is False


# ── cost / cache ──────────────────────────────────────────────────────

def test_cost_maths_and_cache_keys():
    assert prices.spark_usd(1_000_000, 0, 0) == pytest.approx(1.25)
    assert prices.spark_usd(1_000_000, 1_000_000, 0) == pytest.approx(0.15)
    assert prices.sam_usd(images=1) == pytest.approx(0.0025)
    assert prices.sam_usd(frames=10) == pytest.approx(0.01)  # 50-frame floor
    assert prices.voice_usd(3600) == pytest.approx(0.18)
    assert cache_key("spark", {"a": 1}) == cache_key("spark", {"a": 1})
    assert cache_key("spark", {"a": 1}) != cache_key("spark", {"a": 2})

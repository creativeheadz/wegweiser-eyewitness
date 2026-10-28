"""Phase 4 pure tests: polling cap, SAM file lifetime, template days.

No database and no paid calls. The SAM file tests use a fake HTTP
layer in place of requests.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


# -- (a) verdict polling stops after five minutes ----------------------

def test_polling_stops_after_five_minutes():
    js = (ROOT / "app/static/js/eyewitness/capture.js").read_text(
        encoding="utf-8")
    assert "5 * 60 * 1000" in js
    assert "POLL_TIMEOUT" in js
    assert "Checking is taking longer than usual" in js
    # The timeout clears the interval rather than polling for ever.
    assert "clearInterval(poll)" in js


# -- (b) SAM file upload expires and is deleted ------------------------

class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


def test_upload_sends_expires_after_before_file(monkeypatch):
    from app.eyewitness.meta.client import MetaClient
    seen = {}

    def fake_post(url, headers=None, files=None, timeout=None):
        seen["url"] = url
        seen["files"] = files
        return _FakeResp({"id": "file-123"})

    monkeypatch.setattr("requests.post", fake_post)
    client = MetaClient(key="test-key")
    file_id = client.upload_file(b"video-bytes", "clip.mp4")
    assert file_id == "file-123"
    assert seen["url"].endswith("/files")
    names = [name for name, _value in seen["files"]]
    assert names == ["purpose", "expires_after[anchor]",
                     "expires_after[seconds]", "file"]
    fields = dict(seen["files"])
    assert fields["purpose"][1] == "user_data"
    # Bracketed fields: the endpoint ignores a single JSON field.
    assert fields["expires_after[anchor]"][1] == "created_at"
    assert fields["expires_after[seconds]"][1] == "3600"


def test_upload_handles_underscore_id_and_delete(monkeypatch):
    from app.eyewitness.meta.client import MetaClient
    deleted = {}

    def fake_post(url, headers=None, files=None, timeout=None):
        return _FakeResp({"id": "file_abc"})

    def fake_delete(url, headers=None, timeout=None):
        deleted["url"] = url
        return _FakeResp({}, status=200)

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("requests.delete", fake_delete)
    client = MetaClient(key="test-key")
    file_id = client.upload_file(b"x", "clip.mp4")
    assert file_id == "file_abc"
    assert client.delete_file(file_id) is True
    assert deleted["url"].endswith("/files/file_abc")


def test_delete_file_is_best_effort(monkeypatch):
    from app.eyewitness.meta.client import MetaClient

    def boom(url, headers=None, timeout=None):
        raise RuntimeError("network down")

    monkeypatch.setattr("requests.delete", boom)
    client = MetaClient(key="test-key")
    assert client.delete_file("file-123") is False
    assert client.delete_file("") is False


def test_video_reader_deletes_uploaded_file():
    text = (ROOT / "app/eyewitness/readers/video_lights.py").read_text(
        encoding="utf-8")
    assert "delete_file" in text
    assert "_cleanup_sam_file" in text


# -- (c) the open days come from the setting ---------------------------

def test_customer_template_uses_configured_days():
    html = (ROOT / "app/templates/eyewitness/customer.html").read_text(
        encoding="utf-8")
    assert "next_time_days" in html
    assert "for 7 days" not in html
    js = (ROOT / "app/static/js/eyewitness/capture.js").read_text(
        encoding="utf-8")
    assert "data-next-time-days" in js
    assert 'for 7 days' not in js


def test_routes_pass_next_time_days_and_logo():
    text = (ROOT / "app/eyewitness/routes.py").read_text(encoding="utf-8")
    assert "EYEWITNESS_NEXT_TIME_DAYS" in text
    assert "next_time_days" in text
    assert "msp_logo_url" in text
    assert "logo_path" in text


# -- (d) the manual request names the tenant ---------------------------

def test_manual_request_names_the_tenant():
    text = (ROOT / "app/eyewitness/routes.py").read_text(encoding="utf-8")
    # The intake path renders with msp_name; the manual path must too.
    manual = text.split("def manual_request")[1].split("def _deliver")[0]
    assert "msp_name=msp_name" in manual
    assert "Tenants" in manual


# -- mobile polish: one item at a time ---------------------------------

def test_stepper_keeps_list_as_no_script_fallback():
    html = (ROOT / "app/templates/eyewitness/customer.html").read_text(
        encoding="utf-8")
    # Items stay in a list in the markup; script hides all but one.
    assert '<ol class="ew-items">' in html
    assert "data-progress" in html
    assert "data-step-nav" in html
    assert 'aria-live="polite"' in html
    js = (ROOT / "app/static/js/eyewitness/capture.js").read_text(
        encoding="utf-8")
    assert "Step " in js
    assert "data-item-id" in js


def test_large_touch_targets_in_stylesheet():
    css = (ROOT / "app/static/css/eyewitness/customer.css").read_text(
        encoding="utf-8")
    assert "min-height: 3rem" in css
    assert ".ew-logo" in css


# -- Fix pass 1: item state, resume and stepper wiring -------------------

def test_item_done_states_use_the_latest_capture():
    from app.eyewitness.intake import first_unfinished_index, item_done_states
    items = [{"id": "screen_photo"}, {"id": "power_light_video"},
             {"id": "voice_note"}]
    caps = [
        {"item_id": "screen_photo", "kind": "photo", "status": "ok",
         "created_at": 10, "id": 1},
        {"item_id": "power_light_video", "kind": "video",
         "status": "retake", "created_at": 11, "id": 2},
    ]
    states = item_done_states(items, caps)
    assert states == {"screen_photo": True, "power_light_video": False,
                      "voice_note": False}
    assert first_unfinished_index(items, states) == 1
    # A later retake undoes an earlier ok: the latest capture decides.
    caps.append({"item_id": "screen_photo", "kind": "photo",
                 "status": "retake", "created_at": 12, "id": 3})
    states = item_done_states(items, caps)
    assert states["screen_photo"] is False
    assert first_unfinished_index(items, states) == 0
    # Out-of-order rows still resolve by time, then by id.
    caps2 = [
        {"item_id": "screen_photo", "kind": "photo", "status": "ok",
         "created_at": 20, "id": 9},
        {"item_id": "screen_photo", "kind": "photo", "status": "retake",
         "created_at": 10, "id": 8},
    ]
    assert item_done_states(items, caps2)["screen_photo"] is True


def test_item_done_states_skipped_and_answered():
    from app.eyewitness.intake import first_unfinished_index, item_done_states
    items = [{"id": "screen_photo"}, {"id": "q_since_when"},
             {"id": "voice_note"}]
    caps = [
        {"item_id": "screen_photo", "kind": "photo", "status": "skipped",
         "created_at": 10, "id": 1},
        {"item_id": "q_since_when", "kind": "answer", "status": "ok",
         "created_at": 11, "id": 2},
    ]
    states = item_done_states(items, caps)
    assert states == {"screen_photo": True, "q_since_when": True,
                      "voice_note": False}
    # The voice step a customer lands on after accepting the offer.
    assert first_unfinished_index(items, states) == 2
    all_done = item_done_states(items, caps + [
        {"item_id": "voice_note", "kind": "voice", "status": "ok",
         "created_at": 12, "id": 3}])
    assert all(all_done.values())
    # With nothing unfinished the stepper stays on the last step.
    assert first_unfinished_index(items, all_done) == 2
    assert item_done_states(items, []) == {
        "screen_photo": False, "q_since_when": False, "voice_note": False}
    assert first_unfinished_index([], {}) == 0


def _load_demo_module(name):
    import importlib.util
    path = ROOT / "eyewitness" / "demo" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"demo_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_caption_language_warning_names_the_mismatch():
    build_video = _load_demo_module("build_video")
    scenes = [{"number": 8, "caption": "Her voice note, recorded in "
               "German, arrives as an English summary."},
              {"number": 5, "caption": "Brenda opens the link."}]
    warnings = build_video.caption_language_warnings(scenes, {8: "English"})
    assert len(warnings) == 1
    assert warnings[0].startswith("WARNING: scene 8")
    assert "German" in warnings[0] and "English" in warnings[0]
    assert build_video.caption_language_warnings(scenes, {8: "German"}) == []
    assert build_video.caption_language_warnings(scenes, {8: "en"}) != []
    missing = build_video.caption_language_warnings(scenes, {})
    assert "no voice reading" in missing[0]


def test_voice_card_language_comes_from_the_reading():
    overlays = _load_demo_module("overlays")
    assert overlays.language_display("en") == "English"
    assert overlays.language_display("German") == "German"
    assert overlays.language_display("de") == "German"
    assert overlays.language_display("") == ""
    assert overlays.voice_note_line("English") == \
        "Heard in English, kept as a short summary."
    assert overlays.voice_note_line("de") == \
        "Heard in German, kept as an English summary."
    assert "English" in overlays.voice_note_line(None)


def test_record_phone_keeps_the_chrome_sandbox():
    text = (ROOT / "eyewitness/demo/record_phone.py").read_text(
        encoding="utf-8")
    assert "--no-sandbox" not in text
    assert "--use-fake-device-for-media-stream" in text


def test_phone_captions_sit_beside_or_below_and_address_stays_clear():
    text = (ROOT / "eyewitness/demo/build_video.py").read_text(
        encoding="utf-8")
    assert 'visual_rel.startswith("phone/")' in text
    assert "x=680:y=460" in text
    assert "y=1620" in text
    assert "y=h-340" in text
    assert "y=h-220" not in text
    assert 'replace(":",' in text


def test_stepper_advances_focuses_and_resumes():
    html = (ROOT / "app/templates/eyewitness/customer.html").read_text(
        encoding="utf-8")
    assert "item_states" in html
    assert 'data-done="true"' in html
    assert 'tabindex="-1"' in html
    js = (ROOT / "app/static/js/eyewitness/capture.js").read_text(
        encoding="utf-8")
    assert "data-done" in js
    assert ".focus()" in js
    # Got it and Skipped both move to the next unfinished item.
    assert "markDoneAndAdvance" in js
    routes = (ROOT / "app/eyewitness/routes.py").read_text(encoding="utf-8")
    assert "item_done_states" in routes
    assert "item_states=item_states" in routes

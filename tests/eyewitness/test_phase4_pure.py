"""Phase 4 pure tests: polling cap, SAM file lifetime, template days.

No database and no paid calls. The SAM file tests use a fake HTTP
layer in place of requests.
"""
import json
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
    assert names == ["purpose", "expires_after", "file"]
    purpose = dict(seen["files"])["purpose"][1]
    expires = dict(seen["files"])["expires_after"][1]
    assert purpose == "user_data"
    assert json.loads(expires) == {"anchor": "created_at", "seconds": 3600}


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

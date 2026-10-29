"""Phase 6 pure tests: local Glimmer reads, with no paid calls.

The local server sits behind a fake transport, so the payload, the
parsing, the retries and the no-fallback rule run offline. The live
reads against Ollama on this machine sit outside pytest and are
named in the phase checkpoint.
"""
import io
import json
import uuid
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.eyewitness.meta.client import MetaClient
from app.eyewitness.meta.local import (LocalClient, LocalOnly,
                                       build_payload, parse_model_json,
                                       validate_against_schema)
from app.eyewitness.readers.screen import SCREEN_SCHEMA

ROOT = Path(__file__).resolve().parents[2]

SCREEN_OK = {"screen_found": True, "kind": "bsod",
             "text_lines": ["MEMORY_MANAGEMENT (0x1A)"],
             "stop_code": "MEMORY_MANAGEMENT (0x1A)",
             "failing_module": None, "error_codes": ["0x1A"],
             "product_names": [], "language": "en", "legible": True,
             "retake_reason": "none", "instruction_like_text": False,
             "instruction_excerpt": None}


class _Resp:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status

    def json(self):
        return self._body


def _body(content, prompt_tokens=1866, completion_tokens=129):
    return {"choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": prompt_tokens,
                      "completion_tokens": completion_tokens}}


def _client(post_fn, **kwargs):
    logs = []
    client = LocalClient("http://127.0.0.1:11434/v1", post_fn=post_fn,
                         log_call=logs.append, **kwargs)
    return client, logs


# ── the request payload ─────────────────────────────────────────────

def test_payload_shape_content_schema_and_extra_body(monkeypatch):
    monkeypatch.delenv("EYEWITNESS_LOCAL_EXTRA_BODY", raising=False)
    seen = {}

    def post(payload):
        seen.update(payload)
        return _Resp(_body(json.dumps(SCREEN_OK) + "<|eot|>"))

    client, logs = _client(post)
    user = [{"type": "text", "text": "Read this screen photo."},
            {"type": "image_url",
             "image_url": {"url": "data:image/jpeg;base64,AA=="}}]
    out = client.spark_json(system="system text", user_content=user,
                            schema=SCREEN_SCHEMA, kind="spark",
                            capture_id=9)
    assert out["stop_code"] == "MEMORY_MANAGEMENT (0x1A)"
    assert seen["model"] == "eyewitness-glimmer"
    assert seen["messages"][0] == {"role": "system",
                                   "content": "system text"}
    assert seen["messages"][1]["content"] == user
    assert seen["response_format"]["type"] == "json_schema"
    assert seen["response_format"]["json_schema"]["strict"] is True
    assert seen["response_format"]["json_schema"]["schema"] is SCREEN_SCHEMA
    assert seen["max_tokens"] == 1024
    assert seen["temperature"] == 0.2
    assert seen["reasoning_effort"] == "none"
    assert logs[0]["kind"] == "local"
    assert logs[0]["model"] == "eyewitness-glimmer"
    assert logs[0]["usd"] == 0
    assert logs[0]["input_tokens"] == 1866
    assert logs[0]["output_tokens"] == 129
    assert logs[0]["capture_id"] == 9
    assert logs[0]["ms"] >= 0


def test_payload_extra_body_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("EYEWITNESS_LOCAL_EXTRA_BODY",
                       '{"chat_template_kwargs": {"thinking": false}}')
    payload = build_payload(system="s", user_content=[], schema={},
                            model="m")
    assert payload["chat_template_kwargs"] == {"thinking": False}
    assert "reasoning_effort" not in payload


def test_local_client_needs_a_base_url(monkeypatch):
    monkeypatch.delenv("EYEWITNESS_LOCAL_BASE_URL", raising=False)
    client = LocalClient("")
    assert client.is_configured is False
    with pytest.raises(Exception):
        client.spark_json(system="s", user_content=[],
                          schema=SCREEN_SCHEMA)


# ── parsing and schema checks ───────────────────────────────────────

def test_eot_suffix_is_ignored():
    text = json.dumps(SCREEN_OK) + "<|eot|>"
    assert parse_model_json(text) == SCREEN_OK
    assert parse_model_json(json.dumps(SCREEN_OK)) == SCREEN_OK


def test_missing_required_key_raises():
    bad = dict(SCREEN_OK)
    del bad["stop_code"]
    with pytest.raises(ValueError):
        validate_against_schema(bad, SCREEN_SCHEMA)


def test_wrong_type_raises():
    bad = dict(SCREEN_OK, legible="yes")
    with pytest.raises(ValueError):
        validate_against_schema(bad, SCREEN_SCHEMA)
    bad_two = dict(SCREEN_OK, stop_code=12)
    with pytest.raises(ValueError):
        validate_against_schema(bad_two, SCREEN_SCHEMA)


def test_bad_model_object_never_reaches_the_reader():
    def post(payload):
        return _Resp(_body(json.dumps({"screen_found": True})))

    client, _logs = _client(post)
    with pytest.raises(ValueError):
        client.spark_json(system="s", user_content=[],
                          schema=SCREEN_SCHEMA)


# ── retries and the cache ───────────────────────────────────────────

def test_retry_once_on_5xx_and_connection_error():
    calls = {"n": 0}

    def flaky(payload):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Resp({}, status=503)
        return _Resp(_body(json.dumps(SCREEN_OK)))

    client, _logs = _client(flaky)
    out = client.spark_json(system="s", user_content=[],
                            schema=SCREEN_SCHEMA)
    assert out["legible"] is True and calls["n"] == 2

    down = {"n": 0}

    def broken(payload):
        down["n"] += 1
        raise ConnectionError("refused")

    client_two, _logs_two = _client(broken)
    with pytest.raises(ConnectionError):
        client_two.spark_json(system="s", user_content=[],
                              schema=SCREEN_SCHEMA)
    assert down["n"] == 2


def test_no_retry_on_4xx():
    calls = {"n": 0}

    def refused(payload):
        calls["n"] += 1
        return _Resp({}, status=400)

    client, _logs = _client(refused)
    with pytest.raises(RuntimeError):
        client.spark_json(system="s", user_content=[],
                          schema=SCREEN_SCHEMA)
    assert calls["n"] == 1


def test_cached_read_takes_no_time_and_no_second_call():
    calls = {"n": 0}

    def post(payload):
        calls["n"] += 1
        return _Resp(_body(json.dumps(SCREEN_OK)))

    client, logs = _client(post)
    first = client.spark_json(system="s", user_content=[],
                              schema=SCREEN_SCHEMA)
    second = client.spark_json(system="s", user_content=[],
                               schema=SCREEN_SCHEMA)
    assert first == second == SCREEN_OK
    assert calls["n"] == 1
    assert logs[-1]["cache_hit"] is True and logs[-1]["ms"] == 0
    assert logs[-1]["usd"] == 0


# ── cloud-only calls are refused in words ───────────────────────────

def test_supports_flags_and_local_only_errors():
    assert MetaClient.supports_sam is True
    assert MetaClient.supports_transcribe is True
    assert LocalClient.supports_sam is False
    assert LocalClient.supports_transcribe is False
    client, _logs = _client(lambda payload: _Resp(_body("{}")))
    for call in (lambda: client.sam_segment(concept="x", media_part={}),
                 lambda: client.upload_file(b"x"),
                 lambda: client.transcribe(b"x"),
                 lambda: client.image_edit(b"x", "draw")):
        with pytest.raises(LocalOnly):
            call()


# ── backend selection per tenant ────────────────────────────────────

def test_reader_client_follows_the_tenant_setting(monkeypatch):
    from app.eyewitness.meta import client as client_mod
    from app.eyewitness.meta import local as local_mod
    sentinel_local = object()
    sentinel_meta = object()
    monkeypatch.setattr(local_mod, "db_bound_local_client",
                        lambda: sentinel_local)
    monkeypatch.setattr(client_mod, "db_bound_client",
                        lambda: sentinel_meta)
    monkeypatch.setattr(client_mod, "tenant_reader_backend",
                        lambda tenant: "local")
    assert client_mod.reader_client(uuid.uuid4()) is sentinel_local
    monkeypatch.setattr(client_mod, "tenant_reader_backend",
                        lambda tenant: "meta")
    assert client_mod.reader_client(uuid.uuid4()) is sentinel_meta
    # No tenant and no settings row stay on the Meta client.
    assert client_mod.tenant_reader_backend(None) == "meta"


# ── media helpers for the reader tests ──────────────────────────────

def _white_jpeg():
    """A textured photo that passes the server quality gate."""
    import numpy as np
    from PIL import Image
    arr = np.full((800, 1280), 180, dtype=np.uint8)
    for row in range(100, 700, 40):
        arr[row:row + 8, 100:1100:3] = 30
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="JPEG")
    return buf.getvalue()


def _sine_wav(seconds=2.0):
    import math
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        frames = b"".join(
            int(9000 * math.sin(2 * math.pi * 440 * i / 16000))
            .to_bytes(2, "little", signed=True)
            for i in range(int(16000 * seconds)))
        wav.writeframes(frames)
    return buf.getvalue()


def _blink_clip(path):
    import cv2
    import numpy as np
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                             10, (320, 240))
    seq = []
    for colour, count in [((0, 170, 255), 2), ((255, 255, 255), 3)]:
        for _ in range(count):
            seq += [colour] * 4 + [None] * 4
    for i in range(100):
        frame = np.full((240, 320, 3), 30, dtype=np.uint8)
        colour = seq[i % len(seq)]
        if colour is not None:
            cv2.rectangle(frame, (140, 100), (180, 140), colour, -1)
        writer.write(frame)
    writer.release()
    return path.read_bytes()


# ── the no-fallback rule ────────────────────────────────────────────

def test_no_fallback_when_the_local_server_is_down(monkeypatch, tmp_path):
    """Tenant on local, server unreachable: the intake and every
    reader degrade on their own paths, and no MetaClient method runs."""
    meta_calls = []

    def guard(name):
        def blocked(self, *args, **kwargs):
            meta_calls.append(name)
            raise AssertionError(f"MetaClient.{name} called in local mode")
        return blocked

    for name in ("spark_json", "sam_segment", "transcribe",
                 "image_edit", "upload_file"):
        monkeypatch.setattr(MetaClient, name, guard(name))

    def down(payload):
        raise ConnectionError("local server unreachable")

    failing = LocalClient("http://127.0.0.1:9/v1", post_fn=down)
    assert failing.is_configured is True

    from app.eyewitness.readers.screen import read_screen
    from app.eyewitness.readers.sticker import read_sticker
    from app.eyewitness.readers.voice import read_voice
    from app.eyewitness.readers.video_lights import bursts_text, read_video

    screen = read_screen(_white_jpeg(), client=failing)
    assert screen["verdict"] == "ok"
    assert screen["reading"]["summary"] == "photo received"
    sticker = read_sticker(_white_jpeg(), client=failing)
    assert sticker["verdict"] == "ok"
    assert sticker["reading"]["summary"] == "photo received"

    voice = read_voice(_sine_wav(), "audio/wav", client=failing)
    assert voice["verdict"] == "ok"
    assert voice["reading"]["local_only"] is True

    clip = _blink_clip(tmp_path / "blink.mp4")
    video = read_video(clip, "video/mp4", client=failing)
    assert video["verdict"] == "ok"
    assert video["reading"]["locator"] == "code"
    assert "amber 2, white 3" in bursts_text(video["reading"]["bursts"])

    # The Evidence API auto choice with the same failing client.
    from app.eyewitness import api as ew_api
    decision = ew_api.decide_auto(
        "PC keeps crashing with a blue screen", client=failing)
    assert decision["needs_evidence"] is True
    assert "screen_photo" in decision["templates"]

    # The intake agent itself, with its database reads stood in for.
    from app.eyewitness import intake as intake_mod
    tenant = uuid.uuid4()
    run = SimpleNamespace(id=7, tenant_uuid=tenant, helpdesk_ticket_id=3,
                          deviceuuid=None, origin="desk",
                          status="assessing", assessment=None,
                          model=None, error=None, released_at=None)
    rec = SimpleNamespace(id=3, tenant_uuid=tenant, deviceuuid=None,
                          provider="zammad", title="PC crashes",
                          origin="desk",
                          help_request_uuid=None, external_id="9")

    class FakeSession:
        def get(self, model, key):
            return {"IntakeRun": run,
                    "HelpdeskTicket": rec}.get(model.__name__)

        def add(self, row):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

    # Import the model modules before the db stand-in lands, so
    # their own ``from app.models import db`` binds the real object.
    import app.eyewitness.models  # noqa: F401
    import app.models.helpdesk  # noqa: F401
    monkeypatch.setattr("app.models.db",
                        SimpleNamespace(session=FakeSession()))
    monkeypatch.setattr(intake_mod, "_ticket_words",
                        lambda record: "PC keeps crashing, blue screen")
    monkeypatch.setattr(intake_mod, "_device_status", lambda uuid_: None)
    monkeypatch.setattr(intake_mod, "release_run", lambda run_id: True)
    import app.eyewitness.meta.client as client_mod
    monkeypatch.setattr(client_mod, "reader_client",
                        lambda tenant_uuid=None: failing)
    out = intake_mod.assess_run(7)
    assert out == {"released": "intake failed"}
    assert run.status == "failed"
    assert meta_calls == []


# ── the video and voice paths on local ──────────────────────────────

def test_video_local_counts_in_code_without_sam(tmp_path):
    from app.eyewitness.readers.video_lights import bursts_text, read_video

    class LocalFake:
        is_configured = True
        supports_sam = False
        supports_transcribe = False
        backend = "local"

        def spark_json(self, **kwargs):
            return {"description": "a light blinks",
                    "light_visible": True,
                    "instruction_like_text": False,
                    "instruction_excerpt": None}

        def sam_segment(self, **kwargs):
            raise AssertionError("SAM called in local mode")

        def upload_file(self, *args, **kwargs):
            raise AssertionError("upload called in local mode")

    out = read_video(_blink_clip(tmp_path / "blink.mp4"), "video/mp4",
                     client=LocalFake())
    assert out["verdict"] == "ok"
    assert out["reading"]["locator"] == "code"
    assert "amber 2, white 3" in bursts_text(out["reading"]["bursts"])
    assert "Memory" in str(out["reading"]["meaning"])


def test_voice_local_keeps_the_note_without_transcription():
    from app.eyewitness.readers.voice import read_voice

    class LocalFake:
        is_configured = True
        supports_sam = False
        supports_transcribe = False
        backend = "local"

        def transcribe(self, *args, **kwargs):
            raise AssertionError("transcribe called in local mode")

    out = read_voice(_sine_wav(), "audio/wav", client=LocalFake())
    assert out["verdict"] == "ok"
    assert out["reading"]["local_only"] is True
    assert "kept for the technician to listen to" in out["reading"]["summary"]
    assert "No transcription was made" in out["reading"]["summary"]
    assert not out["reading"].get("transcript")


def test_guide_on_local_draws_on_the_server():
    from app.eyewitness.readers.guide import make_guide
    client, _logs = _client(lambda payload: _Resp(_body("{}")))
    out = make_guide(_white_jpeg(), "circle", client=client)
    assert out["method"] == "local-draw"


# ── what people see ─────────────────────────────────────────────────

def test_settings_form_offers_both_backends():
    html = (ROOT / "app/templates/helpdesk/settings.html").read_text(
        encoding="utf-8")
    assert "Meta Model API" in html
    assert "Your own hardware (Muse Glimmer)" in html
    assert "Photos and videos then stay on your network" in html
    assert "voice notes are not transcribed" in html
    assert "data-ew-backend-local" in html
    js = html  # the disabling sits in the settings page script
    assert "localRadio.disabled = true" in js
    assert "data-ew-backend-unavailable" in html


def test_reader_backend_endpoint_refuses_local_without_a_server(
        monkeypatch):
    import app.utilities.app_access_login_required as login_mod
    monkeypatch.setattr(login_mod, "login_required", lambda view: view)
    monkeypatch.delenv("EYEWITNESS_LOCAL_BASE_URL", raising=False)
    from flask import Flask
    from app.eyewitness import api_routes
    from app.eyewitness import models as ew_models

    class FakeSession:
        def get(self, model, key):
            return None

        def add(self, row):
            pass

        def commit(self):
            pass

    monkeypatch.setattr(api_routes, "db",
                        SimpleNamespace(session=FakeSession()))
    monkeypatch.setattr(ew_models, "TenantSettings",
                        type("FakeSettings", (), {}))
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="test")
    app.register_blueprint(api_routes.eyewitness_api_bp)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["tenant_uuid"] = str(uuid.uuid4())
    refused = client.post("/eyewitness/settings/reader-backend",
                          data={"reader_backend": "local"})
    assert refused.status_code == 400
    assert "no local server" in refused.get_json()["error"]


def test_summary_line_names_where_readings_were_made():
    from app.eyewitness.summary import (build_summary, build_summary_html)
    caps = [{"id": 1, "item_title": "screen photo", "kind": "photo",
             "status": "ok", "injection_flag": False,
             "reading": {"summary": "stop screen"}}]
    body = build_summary({"items": [{"title": "Screen photo"}]}, caps,
                         meta_usd=0.0, reader_backend="local")
    line = "Readings were made on your hardware by Muse Glimmer."
    assert line in body
    # Outside the fence: after its closing tag, with the cost line.
    assert body.index(line) > body.index("</customer_evidence")
    assert "Meta cost: $0.0000" in body
    html = build_summary_html({"items": [{"title": "Screen photo"}]}, caps,
                              meta_usd=0.0, reader_backend="local")
    assert line in html
    assert html.index(line) > html.index("&lt;/customer_evidence&gt;")
    meta_body = build_summary({"items": [{"title": "Screen photo"}]}, caps,
                              meta_usd=0.01, reader_backend="meta")
    assert "Readings were made by the Meta Model API." in meta_body


def test_panel_provenance_wording():
    from app.eyewitness.summary import format_read_duration, read_provenance
    assert format_read_duration(78000) == "1 min 18 s"
    assert format_read_duration(45000) == "45 s"
    local = read_provenance({"kind": "local", "ms": 78000,
                             "cache_hit": False})
    assert local == ("Read on your hardware by Muse Glimmer "
                     "in 1 min 18 s.")
    saved = read_provenance({"kind": "local", "ms": 0, "cache_hit": True})
    assert "saved read" in saved
    meta = read_provenance({"kind": "spark", "ms": 3000,
                            "cache_hit": False})
    assert meta == "Read by the Meta Model API in 3 s."
    panel = (ROOT / "app/templates/eyewitness/panel.html").read_text(
        encoding="utf-8")
    assert "provenance" in panel
    assert "not transcribed" in panel


def test_phone_wait_comes_from_the_server():
    html = (ROOT / "app/templates/eyewitness/customer.html").read_text(
        encoding="utf-8")
    assert 'data-read-wait="{{ read_wait or 300 }}"' in html
    assert "data-read-wait-message" in html
    assert ("Checking your photo on your support company's own server. "
            "This can take a couple of minutes.") in html
    js = (ROOT / "app/static/js/eyewitness/capture.js").read_text(
        encoding="utf-8")
    assert 'querySelector("[data-read-wait]")' in js
    assert "READ_WAIT_S * 1000" in js
    assert "WAIT_MESSAGE" in js
    routes = (ROOT / "app/eyewitness/routes.py").read_text(encoding="utf-8")
    assert 'read_wait = 600 if reader_backend == "local" else 300' in routes


def test_migration_adds_reader_backend_additively():
    path = ROOT / "migrations/versions/eyewitness_onprem_20261029.py"
    text = path.read_text(encoding="utf-8")
    assert "reader_backend" in text
    assert "eyewitness_tenant_settings" in text
    assert "server_default='meta'" in text
    assert "drop_table" not in text

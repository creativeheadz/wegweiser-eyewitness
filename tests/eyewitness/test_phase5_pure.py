"""Phase 5 pure and test client tests: no database, no paid calls.

Webhook and API routes run through the Flask test client with
computed signatures and hashed keys. Storage calls sit behind
fakes, so the request path and the auth checks stay under test.
"""
import hashlib
import hmac
import json
import time
import uuid
from types import SimpleNamespace

import pytest

from app.eyewitness import api as ew_api
from app.eyewitness import webhook as ew_hook
from app.utilities.ssrf_guard import SSRFError, assert_safe_url


@pytest.fixture(autouse=True)
def _drop_eyewitness_tables_after():
    """Keep the schema document test free of Eyewitness tables.

    Route tests here import the Eyewitness models, which registers
    their tables in the shared metadata. The committed schema
    document covers the core tables, so the Eyewitness tables come
    back out after each test in this file.
    """
    yield
    try:
        from app.models import db
        for name in [n for n in list(db.metadata.tables)
                     if n.startswith("eyewitness_")]:
            db.metadata.remove(db.metadata.tables[name])
    except Exception:
        pass


# ── webhook signature and payload ───────────────────────────────────

def test_zammad_signature_round_trip_and_tamper():
    body = b'{"ticket": {"id": 42}}'
    secret = "hook-secret-value"
    digest = ew_hook.compute_zammad_signature(body, secret)
    assert ew_hook.verify_zammad_signature(body, f"sha1={digest}", secret)
    assert not ew_hook.verify_zammad_signature(body, f"sha1={digest}", "other")
    assert not ew_hook.verify_zammad_signature(
        body + b" ", f"sha1={digest}", secret)
    assert not ew_hook.verify_zammad_signature(body, None, secret)
    assert not ew_hook.verify_zammad_signature(body, "sha1=abc", None)


def test_extract_ticket_id_shapes():
    assert ew_hook.extract_ticket_id({"ticket": {"id": 7}}) == "7"
    assert ew_hook.extract_ticket_id({"ticket_id": 9}) == "9"
    assert ew_hook.extract_ticket_id({"id": "11"}) == "11"
    assert ew_hook.extract_ticket_id({"article": {"id": 3}}) is None
    assert ew_hook.extract_ticket_id("nope") is None


def test_setup_zammad_webhook_with_fake_admin_calls(monkeypatch):
    calls = []

    class Resp:
        def __init__(self, status, body):
            self.status_code = status
            self._body = body

        def json(self):
            return self._body

    def fake_request(method, url, headers=None, json=None):
        calls.append((method, url, headers, json))
        if url.endswith("/webhooks") and method == "GET":
            return Resp(200, [])
        if url.endswith("/webhooks"):
            return Resp(201, {"id": 5})
        if url.endswith("/triggers") and method == "GET":
            return Resp(200, [])
        if url.endswith("/triggers"):
            return Resp(201, {"id": 9})
        raise AssertionError(url)

    tenant = uuid.uuid4()
    monkeypatch.setattr(ew_hook, "ensure_hook",
                        lambda t: ("hook123", True))
    import app.utilities.secret_manager as secrets_mod
    monkeypatch.setattr(secrets_mod, "get_secret",
                        lambda name: "hmac-value")
    out = ew_hook.setup_zammad_webhook(
        tenant, admin_token="admin-token-value",
        base_url="https://app.example", endpoint="https://desk.example",
        request_fn=fake_request)
    assert out == {"hook_url": "https://app.example/eyewitness/hooks/zammad/hook123",
                   "webhook_id": 5, "trigger_id": 9}
    webhook_call = next(c for c in calls if c[1].endswith("/webhooks")
                        and c[0] == "POST")
    assert webhook_call[3]["endpoint"] == out["hook_url"]
    assert webhook_call[3]["signature_token"] == "hmac-value"
    assert webhook_call[3]["http_method"] == "post"
    assert webhook_call[2]["Authorization"] == "Token token=admin-token-value"
    trigger_call = next(c for c in calls if c[1].endswith("/triggers")
                        and c[0] == "POST")
    assert trigger_call[3]["perform"] == {
        "notification.webhook": {"webhook_id": 5}}
    assert trigger_call[3]["condition"] == {
        "ticket.action": {"operator": "is", "value": "create"}}
    assert "admin-token-value" not in json.dumps(out)


# ── API keys, scopes, tenant isolation ──────────────────────────────

def test_api_key_hash_prefix_and_match():
    plaintext, prefix, hashed = ew_api.generate_api_key()
    assert plaintext.startswith("ew_")
    assert prefix == plaintext[:12] and len(prefix) == 12
    assert hashed == hashlib.sha256(plaintext.encode()).hexdigest()
    assert plaintext not in hashed
    assert ew_api.key_matches(plaintext, hashed)
    assert not ew_api.key_matches(plaintext + "x", hashed)
    row = SimpleNamespace(scopes=["evidence:read"])
    assert ew_api.has_scope(row, "evidence:read")
    assert not ew_api.has_scope(row, "evidence:write")


def test_tenant_isolation_helper():
    tenant_a = uuid.uuid4()
    row = SimpleNamespace(tenant_uuid=tenant_a)
    assert ew_api.visible_to_tenant(row, tenant_a)
    assert ew_api.visible_to_tenant(row, str(tenant_a))
    assert not ew_api.visible_to_tenant(row, uuid.uuid4())
    assert not ew_api.visible_to_tenant(None, tenant_a)


# ── callbacks ───────────────────────────────────────────────────────

def test_callback_signature_and_replay_window():
    body = b'{"event":"evidence.read","request_id":3}'
    secret = "callback-secret"
    headers = ew_api.callback_headers(secret, body, timestamp=1000)
    assert headers["X-Eyewitness-Timestamp"] == "1000"
    assert ew_api.verify_callback(
        secret, headers["X-Eyewitness-Signature"], body, now=1100)
    assert not ew_api.verify_callback(
        secret, headers["X-Eyewitness-Signature"], body, now=2000)
    assert not ew_api.verify_callback(
        secret, headers["X-Eyewitness-Signature"], body + b"x", now=1100)
    assert not ew_api.verify_callback(
        "other", headers["X-Eyewitness-Signature"], body, now=1100)


def test_callback_ssrf_guard_refuses_private_addresses():
    with pytest.raises(SSRFError):
        assert_safe_url("http://127.0.0.1:8000/callback")
    with pytest.raises(SSRFError):
        assert_safe_url("http://169.254.169.254/latest/meta-data")
    with pytest.raises(SSRFError):
        assert_safe_url("ftp://example.com/callback")


# ── auto choice ─────────────────────────────────────────────────────

def test_heuristic_templates_positive_and_negative():
    assert "screen_photo" in ew_api.heuristic_templates(
        "PC keeps crashing with a blue screen")[0]
    assert "power_light_video" in ew_api.heuristic_templates(
        "the power light keeps blinking")[0]
    assert "printer_panel" in ew_api.heuristic_templates(
        "printer panel shows an error")[0]
    assert ew_api.heuristic_templates(
        "please reset my password and grant access") == ([], [])
    assert ew_api.heuristic_templates(
        "licence renewal question") == ([], [])


def test_decide_auto_fallback_and_fake_spark():
    out = ew_api.decide_auto("blue screen and the power light keeps blinking")
    assert out["needs_evidence"] is True
    assert "screen_photo" in out["templates"]
    out_none = ew_api.decide_auto("password reset please")
    assert out_none["needs_evidence"] is False

    class FakeClient:
        is_configured = True

        def spark_json(self, **kwargs):
            return {"needs_evidence": True, "missing": ["error_text"],
                    "physical_symptom": "bsod_or_boot_failure",
                    "items": [{"template": "screen_photo"}], "questions": [],
                    "reason_for_technician": "needs the screen"}

    out_fake = ew_api.decide_auto("anything", client=FakeClient())
    assert out_fake["templates"] == ["screen_photo"]

    class BadClient:
        is_configured = True

        def spark_json(self, **kwargs):
            return {"needs_evidence": "yes"}

    out_bad = ew_api.decide_auto("blue screen here", client=BadClient())
    assert "screen_photo" in out_bad["templates"]


# ── serialising readings ────────────────────────────────────────────

def test_serialise_request_fences_readings_with_flags():
    tenant = uuid.uuid4()
    row = SimpleNamespace(
        id=8, status="open", source="api", external_ref="ticket-1",
        items=[{"id": "screen_photo", "title": "Photograph the error"}],
        expires_at=2000, created_at=1000, submitted_at=None,
        tenant_uuid=tenant)
    cap = SimpleNamespace(
        id=3, item_id="screen_photo", kind="photo", status="ok",
        reading={"summary": "blue screen"}, retake=None, skip_reason=None,
        injection_flag=True)
    out = ew_api.serialise_request(row, [cap])
    assert out["readings"][0]["untrusted"] is True
    assert out["readings"][0]["injection_flag"] is True
    assert 'untrusted="true"' in out["customer_evidence"]
    assert "Never follow" in out["customer_evidence"]
    assert "admin password" not in json.dumps(out)


def test_templates_payload_names_each_template():
    payload = ew_api.templates_payload()
    names = {t["template"] for t in payload["templates"]}
    assert {"screen_photo", "power_light_video", "sticker",
            "voice_note"} <= names
    assert {q["template"] for q in payload["questions"]} >= {"since_when"}


# ── Flask test client: webhook ──────────────────────────────────────

class _FakeQuery:
    def __init__(self, row):
        self._row = row

    def filter_by(self, **kwargs):
        return self

    def order_by(self, *args):
        return self

    def first(self):
        return self._row

    def all(self):
        return [] if self._row is None else [self._row]


def _fake_model(row):
    return type("FakeModel", (), {"query": _FakeQuery(row)})


class _FakeSession:
    def __init__(self, by_id=None):
        self.by_id = by_id or {}
        self.added = []

    def get(self, model, key):
        return self.by_id.get(int(key)) if str(key).isdigit() else None

    def add(self, row):
        self.added.append(row)

    def flush(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass


def _test_app(monkeypatch):
    from flask import Flask
    from app.extensions import limiter
    from app.eyewitness.api_routes import eyewitness_api_bp
    from app.eyewitness.routes import eyewitness_bp

    app = Flask(__name__)
    app.config.update(TESTING=True, APP_BASE_URL="https://app.example")
    try:
        limiter.init_app(app)
    except Exception:
        pass
    app.register_blueprint(eyewitness_bp)
    app.register_blueprint(eyewitness_api_bp)
    return app


def test_webhook_route_with_real_signature(monkeypatch):
    from app.eyewitness import api_routes
    from app.eyewitness import models as ew_models
    import app.utilities.secret_manager as secrets_mod

    tenant = uuid.uuid4()
    settings = SimpleNamespace(tenant_uuid=tenant,
                               webhook_hook_id="hook123")
    monkeypatch.setattr(ew_models, "TenantSettings",
                        _fake_model(settings))
    monkeypatch.setattr(secrets_mod, "get_secret",
                        lambda name: "hmac-value")
    seen = {}

    def fake_ingest(tenant_uuid, external_id):
        seen["call"] = (str(tenant_uuid), external_id)
        return {"ok": True, "created": True, "held": True, "id": 44}

    monkeypatch.setattr(api_routes, "ingest_ticket", fake_ingest)
    app = _test_app(monkeypatch)
    client = app.test_client()
    body = b'{"ticket": {"id": 42}}'
    digest = hmac.new(b"hmac-value", body, hashlib.sha1).hexdigest()
    resp = client.post("/eyewitness/hooks/zammad/hook123", data=body,
                       headers={"X-Hub-Signature": f"sha1={digest}",
                                "Content-Type": "application/json"})
    assert resp.status_code == 200
    assert resp.get_json()["created"] is True
    assert seen["call"] == (str(tenant), "42")

    bad = client.post("/eyewitness/hooks/zammad/hook123", data=body,
                      headers={"X-Hub-Signature": "sha1=deadbeef",
                               "Content-Type": "application/json"})
    assert bad.status_code == 401

    big = client.post("/eyewitness/hooks/zammad/hook123",
                      data=b"x" * (ew_hook.MAX_WEBHOOK_BODY + 1),
                      headers={"X-Hub-Signature": f"sha1={digest}",
                               "Content-Type": "application/json"})
    assert big.status_code == 413


def test_webhook_route_duplicate_is_200(monkeypatch):
    from app.eyewitness import api_routes
    from app.eyewitness import models as ew_models
    import app.utilities.secret_manager as secrets_mod

    tenant = uuid.uuid4()
    settings = SimpleNamespace(tenant_uuid=tenant,
                               webhook_hook_id="hook123")
    monkeypatch.setattr(ew_models, "TenantSettings",
                        _fake_model(settings))
    monkeypatch.setattr(secrets_mod, "get_secret", lambda name: "s3cret")
    monkeypatch.setattr(api_routes, "ingest_ticket",
                        lambda t, e: {"ok": True, "created": False,
                                      "duplicate": True, "id": 7})
    app = _test_app(monkeypatch)
    body = b'{"ticket": {"id": 42}}'
    digest = hmac.new(b"s3cret", body, hashlib.sha1).hexdigest()
    resp = app.test_client().post(
        "/eyewitness/hooks/zammad/hook123", data=body,
        headers={"X-Hub-Signature": f"sha1={digest}",
                 "Content-Type": "application/json"})
    assert resp.status_code == 200
    assert resp.get_json()["duplicate"] is True


# ── Flask test client: Evidence API ─────────────────────────────────

def _key_row(tenant, plaintext):
    return SimpleNamespace(
        id=4, tenant_uuid=tenant, key_prefix=ew_api.prefix_of(plaintext),
        key_hash=ew_api.hash_api_key(plaintext),
        scopes=["evidence:write", "evidence:read"], callback_url=None,
        revoked_at=None, last_used_at=None)


def test_api_templates_and_tenant_isolation(monkeypatch):
    from app.eyewitness import api_routes
    from app.eyewitness import models as ew_models

    tenant = uuid.uuid4()
    plaintext, _prefix, _hashed = ew_api.generate_api_key()
    key_row = _key_row(tenant, plaintext)
    monkeypatch.setattr(ew_models, "ApiKey", _fake_model(key_row))
    monkeypatch.setattr(api_routes, "db",
                        SimpleNamespace(session=_FakeSession()))
    other = SimpleNamespace(
        id=99, tenant_uuid=uuid.uuid4(), status="open", source="api",
        external_ref=None, items=[], expires_at=9999999999,
        created_at=1, submitted_at=None)
    session = _FakeSession(by_id={99: other})
    monkeypatch.setattr(api_routes, "db",
                        SimpleNamespace(session=session))
    # Capture lookup sits empty for the isolation check.
    monkeypatch.setattr(ew_models, "Capture", _fake_model(None))

    app = _test_app(monkeypatch)
    client = app.test_client()
    no_key = client.get("/api/eyewitness/v1/templates")
    assert no_key.status_code == 401
    ok = client.get("/api/eyewitness/v1/templates",
                    headers={"Authorization": f"Bearer {plaintext}"})
    assert ok.status_code == 200
    names = {t["template"] for t in ok.get_json()["templates"]}
    assert "screen_photo" in names
    hidden = client.get("/api/eyewitness/v1/requests/99",
                        headers={"Authorization": f"Bearer {plaintext}"})
    assert hidden.status_code == 404


def test_api_create_returns_template_text_and_link(monkeypatch):
    from app.eyewitness import api_routes
    from app.eyewitness import models as ew_models

    tenant = uuid.uuid4()
    plaintext, _prefix, _hashed = ew_api.generate_api_key()
    key_row = _key_row(tenant, plaintext)
    monkeypatch.setattr(ew_models, "ApiKey", _fake_model(key_row))

    class FakeRequest:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.id = 55

    session = _FakeSession()
    session.get = lambda model, key: SimpleNamespace(
        tenantname="Example Support")  # Tenants lookup
    monkeypatch.setattr(api_routes, "db",
                        SimpleNamespace(session=session))
    monkeypatch.setattr(ew_models, "EvidenceRequest", FakeRequest)

    app = _test_app(monkeypatch)
    resp = app.test_client().post(
        "/api/eyewitness/v1/requests",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={"templates": ["screen_photo"], "external_ref": "ticket-9"})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["link"] == (
        "https://app.example/eyewitness/"
        + session.added[0].token)
    assert "/eyewitness/" in body["customer_text"]
    assert "wegcoin" not in body["customer_text"].lower()
    assert body["expires_at"] > int(time.time())
    assert session.added[0].tenant_uuid == tenant
    assert session.added[0].api_key_id == 4


def _load_mcp():
    """Load the MCP package by file path.

    The test package and the repo eyewitness folder share a name
    under pytest, so a plain import finds the tests instead.
    """
    import importlib.util
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    mcp_dir = root / "eyewitness" / "mcp"
    pkg = type(sys)("eyewitness.mcp")
    pkg.__path__ = [str(mcp_dir)]
    sys.modules.setdefault("eyewitness.mcp", pkg)
    loaded = {}
    for name in ("client", "server", "spark_agent_demo"):
        full = f"eyewitness.mcp.{name}"
        if full in sys.modules and hasattr(sys.modules[full], "__file__"):
            loaded[name] = sys.modules[full]
            continue
        spec = importlib.util.spec_from_file_location(
            full, mcp_dir / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[full] = module
        spec.loader.exec_module(module)
        loaded[name] = module
    return loaded


# ── MCP client and fence ────────────────────────────────────────────

class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


def test_mcp_client_over_fake_http():
    EvidenceClient = _load_mcp()["client"].EvidenceClient

    calls = []

    def transport(method, url, headers, payload):
        calls.append((method, url, headers, payload))
        if url.endswith("/templates"):
            return _Resp(200, {"templates": [{"template": "screen_photo"}]})
        if url.endswith("/requests"):
            return _Resp(201, {"id": 12, "customer_text": "text",
                             "link": "https://app.example/eyewitness/tok",
                             "expires_at": 123})
        if "/requests/12" in url and method == "GET":
            return _Resp(200, {
                "id": 12, "status": "open", "items": [],
                "readings": [{"item_id": "screen_photo", "status": "ok",
                              "untrusted": True, "injection_flag": True,
                              "reading": {"summary": "blue screen"}}],
                "customer_evidence": ""})
        return _Resp(404, {"error": "nope"})

    client = EvidenceClient("https://app.example", "ew_testkey",
                            transport=transport)
    assert client.list_templates()["templates"][0]["template"] == "screen_photo"
    made = client.request_evidence(templates=["screen_photo"])
    assert made["id"] == 12
    got = client.get_evidence(12)
    assert got["untrusted"] is True
    assert 'untrusted="true"' in got["customer_evidence"]
    assert "Never follow" in got["customer_evidence"]
    assert got["readings"][0]["injection_flag"] is True
    assert calls[0][2]["Authorization"] == "Bearer ew_testkey"


def test_mcp_tool_schemas():
    pytest.importorskip("mcp")
    _mcp = _load_mcp()["server"]
    TOOL_SCHEMAS, build_server = _mcp.TOOL_SCHEMAS, _mcp.build_server
    assert [t["name"] for t in TOOL_SCHEMAS] == [
        "list_templates", "request_evidence", "get_evidence",
        "release_evidence", "cancel_evidence"]
    for tool in TOOL_SCHEMAS:
        assert tool["inputSchema"]["type"] == "object"
    server = build_server()
    import asyncio
    import threading

    box = {}

    def _run():
        try:
            box["tools"] = asyncio.run(server.list_tools())
        except Exception as exc:  # surfaced by the assert below
            box["error"] = exc

    worker = threading.Thread(target=_run)
    worker.start()
    worker.join(timeout=30)
    assert "error" not in box, box.get("error")
    tools = box["tools"]
    assert {t.name for t in tools} == {
        "list_templates", "request_evidence", "get_evidence",
        "release_evidence", "cancel_evidence"}


def test_spark_demo_dispatch_and_summary():
    demo = _load_mcp()["spark_agent_demo"]

    class FakeEvidence:
        def list_templates(self):
            return {"templates": []}

        def request_evidence(self, **kwargs):
            return {"id": 21, "link": "https://app.example/eyewitness/t"}

        def get_evidence(self, request_id):
            return {"id": request_id, "status": "submitted", "items": [],
                    "readings": [{"item_id": "screen_photo",
                                  "status": "ok", "injection_flag": False,
                                  "reading": {"summary": "blue screen"}}],
                    "customer_evidence": ""}

        def release_evidence(self, request_id):
            return {"released": True}

        def cancel_evidence(self, request_id):
            return {"status": "cancelled"}

    client = FakeEvidence()
    assert demo.dispatch_tool(client, "request_evidence",
                              {"templates": ["screen_photo"]})["id"] == 21

    rounds = {"n": 0}

    def fake_chat(messages, tools=None, http_post=None):
        rounds["n"] += 1
        if tools:
            if rounds["n"] == 1:
                return {"choices": [{"message": {
                    "tool_calls": [{
                        "id": "c1", "function": {
                            "name": "request_evidence",
                            "arguments": json.dumps(
                                {"auto": True,
                                 "ticket_text": "blue screen"})}}]}}]}
            return {"choices": [{"message": {"content": "requested"}}]}
        return {"choices": [{"message": {
            "content": "Summary: the screen photo shows a blue screen."}}]}

    out = demo.run_demo("blue screen", evidence_client=client,
                        chat=fake_chat, poll_interval=0, max_polls=2,
                        sleep=lambda s: None)
    assert out["request_id"] == 21
    assert "blue screen" in out["summary"]

"""Eyewitness's own Meta client: cache, daily cap, cost log, one retry.

The key is Eyewitness's own (`EYEWITNESS_META_API_KEY`), read only here.
It deliberately does NOT go through the tenant AI config or
`_non_production_guard`: that guard stops the broad AI features from
spending outside production, while Eyewitness has an explicit separate
key and a hard daily cap (`EYEWITNESS_DAILY_USD_CAP`, default 5.00).
No key set means "not configured": callers skip the paid steps, log it,
and release tickets to the governor unchanged. Never contributor models
for customer evidence.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time

from . import prices

logger = logging.getLogger(__name__)

BASE_URL = "https://api.meta.ai/v1"
DEFAULT_SPARK_MODEL = "muse-spark-1.3"
SPARK_MIN_TOKENS = 6144  # Muse Spark always reasons; see ai_provider.py


def api_key() -> str:
    return (os.environ.get("EYEWITNESS_META_API_KEY") or "").strip()


def configured() -> bool:
    return bool(api_key())


def cache_key(kind: str, payload) -> str:
    raw = json.dumps({"kind": kind, "payload": payload},
                     sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def daily_cap_usd() -> float:
    try:
        return float(os.environ.get("EYEWITNESS_DAILY_USD_CAP", "5.00"))
    except ValueError:
        return 5.00


class MetaNotConfigured(RuntimeError):
    pass


class DailyCapExceeded(RuntimeError):
    pass


class MetaClient:
    """One client for every Eyewitness Meta call.

    `log_call`/`cache_get`/`cache_put`/`spent_today` are injectable so the
    spike and the pure tests run without a database. In the app they are
    bound to eyewitness_meta_calls / eyewitness_meta_cache.
    """

    backend = "meta"
    supports_sam = True
    supports_transcribe = True

    def __init__(self, key: str | None = None, *, log_call=None,
                 cache_get=None, cache_put=None, spent_today=None):
        self._key = api_key() if key is None else key
        self._log_call = log_call
        self._cache_get = cache_get
        self._cache_put = cache_put
        self._spent_today = spent_today
        self._mem_cache: dict = {}

    @property
    def is_configured(self) -> bool:
        return bool(self._key)

    @property
    def model_name(self) -> str:
        return os.environ.get("EYEWITNESS_SPARK_MODEL", DEFAULT_SPARK_MODEL)

    def _spent(self) -> float:
        return float(self._spent_today()) if self._spent_today else 0.0

    def _check_cap(self, estimate: float = 0.0) -> None:
        if self._spent() + estimate > daily_cap_usd():
            raise DailyCapExceeded("Eyewitness daily Meta cap reached")

    def _cached(self, key: str):
        if os.environ.get("EYEWITNESS_CACHE", "on").lower() in ("0", "off", "false"):
            return None
        if self._cache_get:
            return self._cache_get(key)
        return self._mem_cache.get(key)

    def _store(self, key: str, value) -> None:
        if self._cache_put:
            self._cache_put(key, value)
        else:
            self._mem_cache[key] = value

    def _record(self, kind, model, usage, units, usd, cache_hit, ms,
                intake_run_id=None, capture_id=None) -> None:
        row = {"kind": kind, "model": model,
               "input_tokens": int(usage.get("input_tokens") or 0),
               "cached_tokens": int(usage.get("cached_tokens") or 0),
               "output_tokens": int(usage.get("output_tokens") or 0),
               "units": int(units or 0), "usd": float(usd),
               "cache_hit": bool(cache_hit), "ms": int(ms)}
        if self._log_call:
            try:
                self._log_call({**row, "intake_run_id": intake_run_id,
                                "capture_id": capture_id})
                return
            except Exception:  # logging must never break the read
                logger.exception("eyewitness meta log failed")
        logger.info("eyewitness meta %s %s usd=%.5f cache=%s",
                    kind, model, usd, cache_hit)

    def spark_json(self, *, system: str, user_content, schema: dict,
                   model: str | None = None, kind: str = "spark",
                   intake_run_id=None, capture_id=None) -> dict:
        """One Spark structured-output call. Returns the parsed JSON dict.

        `user_content` is an OpenAI content list (text / image_url parts).
        Raises MetaNotConfigured with no key; callers degrade gracefully.
        """
        if not self.is_configured:
            raise MetaNotConfigured("not configured")
        model = model or os.environ.get("EYEWITNESS_SPARK_MODEL",
                                        DEFAULT_SPARK_MODEL)
        if "-contributor" in model:
            raise ValueError("contributor models never read customer evidence")
        key = cache_key(kind, {"model": model, "system": system,
                               "user": user_content, "schema": schema})
        hit = self._cached(key)
        if hit is not None:
            self._record(kind, model, {}, 0, 0.0, True, 0,
                         intake_run_id, capture_id)
            return hit if isinstance(hit, dict) else json.loads(hit)
        self._check_cap()
        from openai import OpenAI
        client = OpenAI(api_key=self._key, base_url=BASE_URL)
        started = time.time()
        last_exc = None
        for attempt in (1, 2):
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user_content}],
                    max_tokens=max(SPARK_MIN_TOKENS, 6144),
                    response_format={"type": "json_schema",
                                     "json_schema": {"name": "eyewitness",
                                                     "schema": schema,
                                                     "strict": True}},
                    extra_body={"reasoning_effort": "minimal"})
                break
            except Exception as exc:  # retry once on 429/5xx only
                last_exc = exc
                status = getattr(exc, "status_code", None)
                if attempt == 2 or (status is not None
                                    and status < 500 and status != 429):
                    raise
                time.sleep(1.0)
        else:
            raise last_exc  # pragma: no cover
        ms = int((time.time() - started) * 1000)
        usage = getattr(resp, "usage", None)
        usage_d = {"input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                   "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
                   "cached_tokens": 0}
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            usage_d["cached_tokens"] = getattr(details, "cached_tokens", 0) or 0
        text = resp.choices[0].message.content or "{}"
        parsed = json.loads(text)
        usd = prices.usd_for(kind, **usage_d)
        self._store(key, parsed)
        self._record(kind, model, usage_d, 0, usd, False, ms,
                     intake_run_id, capture_id)
        return parsed

    def sam_segment(self, *, concept: str, media_part: dict,
                    intake_run_id=None, capture_id=None,
                    frame_count: int = 0) -> str:
        """One SAM 3.1 concept on one image or video. Returns raw text.

        `media_part` is an OpenAI Responses content part (input_image /
        input_video). Streamed; the concatenated text is what
        sam_parser.parse_sam_text consumes. Video billing is per frame
        with a 50 frame floor, so the caller passes the frame count of
        the normalised clip when it knows it.
        """
        if not self.is_configured:
            raise MetaNotConfigured("not configured")
        key = cache_key("sam", {"concept": concept, "media": media_part})
        hit = self._cached(key)
        if hit is not None:
            self._record("sam", "sam-3.1", {}, 0, 0.0, True, 0,
                         intake_run_id, capture_id)
            return hit if isinstance(hit, str) else json.dumps(hit)
        self._check_cap(estimate=0.02)
        import requests
        started = time.time()
        last_exc = None
        resp = None
        for attempt in (1, 2):
            try:
                resp = requests.post(
                    f"{BASE_URL}/responses",
                    headers={"Authorization": f"Bearer {self._key}",
                             "Content-Type": "application/json"},
                    json={"model": "sam-3.1", "stream": True,
                          "input": [{"role": "user", "content": [
                              {"type": "input_text", "text": concept},
                              media_part]}],
                          "metadata": {"mask_encoding": "one_bit",
                                       "include_confidence": "true"}},
                    timeout=180, stream=True)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise RuntimeError(f"SAM returned {resp.status_code}")
                break
            except Exception as exc:
                last_exc = exc
                if attempt == 2:
                    raise
                time.sleep(1.0)
        if resp is None:  # pragma: no cover
            raise last_exc
        if resp.status_code >= 400:
            raise RuntimeError(f"SAM returned {resp.status_code}")
        text = resp.text
        ms = int((time.time() - started) * 1000)
        frames = int(frame_count or 0)
        self._store(key, text)
        self._record("sam", "sam-3.1", {}, frames,
                     prices.sam_usd(frames=frames
                                      or SAM_MIN_FRAMES_FALLBACK),
                     False, ms, intake_run_id, capture_id)
        return text

    def upload_file(self, data: bytes, filename: str = "clip.mp4",
                    *, intake_run_id=None, capture_id=None) -> str:
        """Upload one file for a Responses call. Returns the file id.

        Used when a video is too large for a data URL. The upload itself
        is not billed; the SAM call that uses the file is. The file is
        set to expire one hour after creation, and the caller deletes
        it once the SAM calls end (see delete_file).
        """
        if not self.is_configured:
            raise MetaNotConfigured("not configured")
        import requests
        # purpose and expires_after go before the file part. The expiry
        # takes bracketed form fields: a single JSON field is accepted
        # with HTTP 200 and ignored, so the file never expires (checked
        # against the live endpoint, 28 Oct).
        files = [
            ("purpose", (None, "user_data")),
            ("expires_after[anchor]", (None, "created_at")),
            ("expires_after[seconds]", (None, "3600")),
            ("file", (filename, data, "video/mp4")),
        ]
        resp = requests.post(
            f"{BASE_URL}/files",
            headers={"Authorization": f"Bearer {self._key}"},
            files=files, timeout=180)
        if resp.status_code >= 400:
            raise RuntimeError(f"file upload returned {resp.status_code}")
        file_id = (resp.json() or {}).get("id")
        if not file_id:
            raise RuntimeError("file upload returned no id")
        return str(file_id)

    def delete_file(self, file_id: str) -> bool:
        """Delete one uploaded file. Best effort: never raises.

        The upload returns ids in more than one shape (for example
        file-123 and file_abc); the id is used as given.
        """
        if not file_id or not self.is_configured:
            return False
        try:
            import requests
            resp = requests.delete(
                f"{BASE_URL}/files/{file_id}",
                headers={"Authorization": f"Bearer {self._key}"},
                timeout=30)
            return resp.status_code < 400
        except Exception:
            return False

    def transcribe(self, wav_bytes: bytes, *, duration_s: float = 0.0,
                   keywords=None, intake_run_id=None,
                   capture_id=None) -> dict:
        """One batch Voice Transcribe call. Returns the parsed body.

        The endpoint takes multipart form data: a JSON `request` part
        and a mono WAV `audio` part at 16 kHz or 24 kHz. There is no
        detected language field in the response; the Spark summary
        step names the language. A 400 means the WAV was refused.
        """
        if not self.is_configured:
            raise MetaNotConfigured("not configured")
        import hashlib
        digest = hashlib.sha256(wav_bytes or b"").hexdigest()
        req_obj = {"model": "muse-voice-transcribe-1.0",
                   "audioEncoding": "WAV", "mode": "PUSH_TO_TALK"}
        if keywords:
            req_obj["keywords"] = list(keywords)[:20]
        key = cache_key("voice", {"digest": digest, "request": req_obj})
        hit = self._cached(key)
        if hit is not None:
            self._record("voice", "muse-voice-transcribe-1.0", {}, 0,
                         0.0, True, 0, intake_run_id, capture_id)
            return hit if isinstance(hit, dict) else json.loads(hit)
        self._check_cap(estimate=prices.voice_usd(duration_s or 60))
        import requests
        started = time.time()
        last_exc = None
        parsed = None
        for attempt in (1, 2):
            try:
                resp = requests.post(
                    f"{BASE_URL}/asr/transcribe",
                    headers={"Authorization": f"Bearer {self._key}",
                             "Accept": "application/json"},
                    files={
                        "request": (None, json.dumps(req_obj),
                                    "application/json"),
                        "audio": ("voice.wav", wav_bytes, "audio/wav")},
                    timeout=180)
                if resp.status_code == 400:
                    raise ValueError("voice endpoint refused the WAV")
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise RuntimeError(
                        f"voice returned {resp.status_code}")
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"voice returned {resp.status_code}")
                parsed = resp.json()
                break
            except ValueError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt == 2:
                    raise
                time.sleep(1.0)
        if parsed is None:  # pragma: no cover
            raise last_exc
        ms = int((time.time() - started) * 1000)
        seconds = duration_s
        try:
            seconds = float(parsed.get("audioDurationMs") or 0) / 1000.0 \
                or seconds
        except (TypeError, ValueError):
            pass
        units = int(round(seconds))
        self._store(key, parsed)
        self._record("voice", "muse-voice-transcribe-1.0", {}, units,
                     prices.voice_usd(seconds), False, ms,
                     intake_run_id, capture_id)
        return parsed

    def image_edit(self, image_bytes: bytes, prompt: str, *,
                   intake_run_id=None, capture_id=None) -> bytes:
        """One Muse Image edit on a customer photo. Returns image bytes.

        Technician triggered only (S2). The result never goes to a
        customer on its own; it joins a new request that waits for
        approval. Raises when the endpoint refuses the edit so the
        caller can fall back to a local annotation.
        """
        if not self.is_configured:
            raise MetaNotConfigured("not configured")
        self._check_cap(estimate=prices.image_usd(1))
        import base64
        import io as _io
        from openai import OpenAI
        client = OpenAI(api_key=self._key, base_url=BASE_URL)
        started = time.time()
        bio = _io.BytesIO(image_bytes)
        bio.name = "photo.jpg"
        resp = client.images.edit(model="muse-image-1.0", image=bio,
                                  prompt=prompt, n=1, size="1024x1024")
        ms = int((time.time() - started) * 1000)
        self._record("image", "muse-image-1.0", {}, 1,
                     prices.image_usd(1), False, ms,
                     intake_run_id, capture_id)
        data = (resp.data or [None])[0]
        b64 = getattr(data, "b64_json", None) if data else None
        if b64:
            return base64.b64decode(b64)
        url = getattr(data, "url", None) if data else None
        if url:
            import requests
            got = requests.get(url, timeout=60)
            got.raise_for_status()
            return got.content
        raise RuntimeError("image edit returned no image")


SAM_MIN_FRAMES_FALLBACK = 50


def db_bound_client() -> MetaClient:
    """A client bound to the eyewitness_meta_* tables (app context)."""
    from app.models import db
    from app.eyewitness.models import MetaCache, MetaCall

    def _get(k):
        row = MetaCache.query.get(k)
        if row is None:
            return None
        try:
            return json.loads(row.response)
        except (TypeError, ValueError):
            return row.response

    def _put(k, v):
        row = MetaCache.query.get(k) or MetaCache(key=k)
        row.response = v if isinstance(v, str) else json.dumps(v)
        row.created_at = int(time.time())
        db.session.add(row)
        db.session.commit()

    def _log(d):
        db.session.add(MetaCall(
            intake_run_id=d.get("intake_run_id"),
            capture_id=d.get("capture_id"), kind=d["kind"], model=d["model"],
            input_tokens=d["input_tokens"], cached_tokens=d["cached_tokens"],
            output_tokens=d["output_tokens"], units=d["units"], usd=d["usd"],
            cache_hit=d["cache_hit"], ms=d["ms"],
            created_at=int(time.time())))
        db.session.commit()

    def _spent():
        start = int(time.time()) - int(time.time()) % 86400
        total = (db.session.query(db.func.coalesce(db.func.sum(MetaCall.usd), 0))
                 .filter(MetaCall.created_at >= start).scalar())
        return float(total or 0)

    return MetaClient(log_call=_log, cache_get=_get, cache_put=_put,
                      spent_today=_spent)


def tenant_reader_backend(tenant_uuid) -> str:
    """``local`` or ``meta`` for one tenant (Phase 6).

    Absence of a settings row, a missing table, or no app context all
    mean ``meta``: the setting is opt-in per tenant and nothing may
    switch a tenant to the local server by accident.
    """
    if tenant_uuid is None:
        return "meta"
    try:
        import uuid as uuid_mod
        from app.models import db
        from app.eyewitness.models import TenantSettings
        try:
            key = uuid_mod.UUID(str(tenant_uuid))
        except (ValueError, AttributeError):
            key = tenant_uuid
        row = db.session.get(TenantSettings, key)
        if row is not None and getattr(row, "reader_backend", "meta") == "local":
            return "local"
    except Exception:
        return "meta"
    return "meta"


def reader_client(tenant_uuid=None):
    """The reader client for one tenant: local, or the Meta client.

    Every call site that handles customer evidence or ticket text
    takes its client here. A tenant set to ``local`` gets the
    LocalClient and nothing else: there is no fallback to the Meta
    Model API, even when the local server is down.
    """
    if tenant_reader_backend(tenant_uuid) == "local":
        from .local import db_bound_local_client
        return db_bound_local_client()
    return db_bound_client()

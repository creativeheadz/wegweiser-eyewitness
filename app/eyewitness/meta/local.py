"""Local reader client: Muse Glimmer on the tenant's own hardware.

Phase 6. A tenant set to ``local`` reads every photo and clip with
Muse Glimmer (Meta open weights) behind an OpenAI-compatible server
on its own network: Ollama, llama.cpp's ``llama-server``, vLLM, or a
partner host. The request shape is the one measured on Hera against
Ollama 0.40.2: chat completions, a JSON schema with ``strict``, and
``reasoning_effort`` switched off through ``EYEWITNESS_LOCAL_EXTRA_BODY``
because llama.cpp and vLLM name that switch differently.

There is no fallback to the Meta Model API in this module, and none
in the callers that pick this client. When the local server fails the
call raises and the readers take their degraded path: the capture is
kept and the technician reads it.

The model answer can end with a stray ``<|eot|>`` after the JSON, so
parsing uses ``raw_decode`` and ignores whatever follows the object.
"""
from __future__ import annotations

import json
import logging
import os
import time

from .client import MetaNotConfigured, cache_key

logger = logging.getLogger(__name__)

DEFAULT_LOCAL_MODEL = "eyewitness-glimmer"
PLACEHOLDER_KEY = "eyewitness-local"
LOG_KIND = "local"


class LocalOnly(RuntimeError):
    """A cloud-only Meta call was asked of the local client."""


def local_base_url() -> str:
    return (os.environ.get("EYEWITNESS_LOCAL_BASE_URL") or "").strip()


def local_model() -> str:
    return (os.environ.get("EYEWITNESS_LOCAL_MODEL")
            or DEFAULT_LOCAL_MODEL).strip() or DEFAULT_LOCAL_MODEL


def local_timeout_s() -> float:
    try:
        return float(os.environ.get("EYEWITNESS_LOCAL_TIMEOUT_S", "600"))
    except ValueError:
        return 600.0


def local_extra_body() -> dict:
    raw = (os.environ.get("EYEWITNESS_LOCAL_EXTRA_BODY") or "").strip()
    if not raw:
        return {"reasoning_effort": "none"}
    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.warning("eyewitness local: EYEWITNESS_LOCAL_EXTRA_BODY "
                       "is not JSON, using the default")
        return {"reasoning_effort": "none"}
    return parsed if isinstance(parsed, dict) else {"reasoning_effort": "none"}


def parse_model_json(text: str):
    """The first JSON value in a model answer, or a raise.

    Glimmer ends its answer with a stray ``<|eot|>`` after the object,
    which ``json.loads`` refuses. ``raw_decode`` takes the object and
    leaves the rest.
    """
    stripped = (text or "").strip()
    value, _end = json.JSONDecoder().raw_decode(stripped)
    return value


_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float))
    and not isinstance(v, bool),
    "null": lambda v: v is None,
}


def validate_against_schema(parsed, schema: dict) -> dict:
    """Check the required keys and their types before a read is used.

    A missing key or a wrong type raises, and the reader's failure
    path takes over, as it does for a failed call.
    """
    if not isinstance(parsed, dict):
        raise ValueError("local read did not return an object")
    schema = schema or {}
    properties = schema.get("properties") or {}
    for key in schema.get("required") or []:
        if key not in parsed:
            raise ValueError(f"local read missed the required key {key}")
        declared = (properties.get(key) or {}).get("type")
        if declared is None:
            continue
        names = declared if isinstance(declared, list) else [declared]
        if not any(_TYPE_CHECKS.get(name, lambda v: True)(parsed[key])
                   for name in names):
            raise ValueError(f"local read gave the wrong type for {key}")
    return parsed


def build_payload(*, system: str, user_content, schema: dict,
                  model: str) -> dict:
    """One chat completions body for the local server (pure)."""
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user_content}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "eyewitness",
                                            "schema": schema,
                                            "strict": True}},
        "max_tokens": 1024,
        "temperature": 0.2,
    }
    payload.update(local_extra_body())
    return payload


class LocalClient:
    """The ``MetaClient.spark_json`` contract against a local server.

    Cache and call log are the same tables as the Meta client's, so
    the technician panel and the summary note show the read and its
    time. Local reads cost nothing: ``usd`` is always 0 and the logged
    kind is ``local``. ``post_fn`` is injectable for the pure tests.
    """

    backend = "local"
    supports_sam = False
    supports_transcribe = False

    def __init__(self, base_url: str | None = None, *, model: str | None = None,
                 key: str | None = None, log_call=None, cache_get=None,
                 cache_put=None, spent_today=None, post_fn=None,
                 timeout_s: float | None = None):
        self._base_url = (local_base_url() if base_url is None
                          else base_url).rstrip("/")
        self._model = model or local_model()
        if key is None:
            key = (os.environ.get("EYEWITNESS_LOCAL_API_KEY") or "").strip()
        self._key = key or PLACEHOLDER_KEY
        self._log_call = log_call
        self._cache_get = cache_get
        self._cache_put = cache_put
        self._post_fn = post_fn
        self._timeout_s = (local_timeout_s() if timeout_s is None
                           else float(timeout_s))
        self._mem_cache: dict = {}

    @property
    def is_configured(self) -> bool:
        return bool(self._base_url)

    @property
    def model_name(self) -> str:
        return self._model

    def _cached(self, key: str):
        if os.environ.get("EYEWITNESS_CACHE", "on").lower() in (
                "0", "off", "false"):
            return None
        if self._cache_get:
            return self._cache_get(key)
        return self._mem_cache.get(key)

    def _store(self, key: str, value) -> None:
        if self._cache_put:
            self._cache_put(key, value)
        else:
            self._mem_cache[key] = value

    def _record(self, usage, cache_hit, ms, intake_run_id=None,
                capture_id=None) -> None:
        row = {"kind": LOG_KIND, "model": self._model,
               "input_tokens": int((usage or {}).get("input_tokens") or 0),
               "cached_tokens": int((usage or {}).get("cached_tokens") or 0),
               "output_tokens": int((usage or {}).get("output_tokens") or 0),
               "units": 0, "usd": 0.0, "cache_hit": bool(cache_hit),
               "ms": int(ms)}
        if self._log_call:
            try:
                self._log_call({**row, "intake_run_id": intake_run_id,
                                "capture_id": capture_id})
                return
            except Exception:  # logging must never break the read
                logger.exception("eyewitness local log failed")
        logger.info("eyewitness local %s usd=0 cache=%s", self._model,
                    cache_hit)

    def _post(self, payload: dict):
        if self._post_fn is not None:
            return self._post_fn(payload)
        import requests
        return requests.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._key}",
                     "Content-Type": "application/json"},
            json=payload, timeout=self._timeout_s)

    def spark_json(self, *, system: str, user_content, schema: dict,
                   model: str | None = None, kind: str = "spark",
                   intake_run_id=None, capture_id=None) -> dict:
        """One Glimmer structured read. Same contract as Meta's.

        One retry on a connection error or a 5xx, none on a 4xx.
        Raises ``MetaNotConfigured`` with no base URL set, so a tenant
        set to ``local`` with no server named degrades in the same
        place a missing Meta key does.
        """
        if not self.is_configured:
            raise MetaNotConfigured("local server not configured")
        model = model or self._model
        key = cache_key(LOG_KIND, {"model": model, "system": system,
                                   "user": user_content, "schema": schema})
        hit = self._cached(key)
        if hit is not None:
            self._record({}, True, 0, intake_run_id, capture_id)
            return hit if isinstance(hit, dict) else json.loads(hit)
        payload = build_payload(system=system, user_content=user_content,
                                schema=schema, model=model)
        started = time.time()
        body = None
        last_exc = None
        for attempt in (1, 2):
            try:
                resp = self._post(payload)
                status = getattr(resp, "status_code", 200) or 200
                if status >= 500:
                    raise RuntimeError(f"local server returned {status}")
                if status >= 400:
                    raise RuntimeError(f"local server returned {status}")
                body = resp.json()
                break
            except Exception as exc:
                last_exc = exc
                status = getattr(exc, "status_code", None)
                text = str(exc)
                if "local server returned " in text:
                    try:
                        status = int(text.rsplit(" ", 1)[-1])
                    except ValueError:
                        status = None
                if attempt == 2 or (status is not None
                                    and 400 <= status < 500):
                    raise
                time.sleep(0.5)
        if body is None:  # pragma: no cover
            raise last_exc
        ms = int((time.time() - started) * 1000)
        usage_raw = body.get("usage") or {}
        usage = {"input_tokens": usage_raw.get("prompt_tokens") or 0,
                 "output_tokens": usage_raw.get("completion_tokens") or 0,
                 "cached_tokens": 0}
        choices = body.get("choices") or []
        content = ""
        if choices:
            content = ((choices[0].get("message") or {}).get("content")
                       or "")
        if isinstance(content, list):
            content = "".join(str(part.get("text") or "")
                              for part in content if isinstance(part, dict))
        parsed = validate_against_schema(parse_model_json(str(content)),
                                          schema)
        self._store(key, parsed)
        self._record(usage, False, ms, intake_run_id, capture_id)
        return parsed

    # Cloud-only calls. Anything that reaches for one of these on a
    # local tenant is refused in words, never sent anywhere.
    def sam_segment(self, **kwargs):
        raise LocalOnly("SAM runs on the Meta Model API, and this "
                        "tenant reads on its own hardware")

    def upload_file(self, *args, **kwargs):
        raise LocalOnly("file upload runs on the Meta Model API, and "
                        "this tenant reads on its own hardware")

    def delete_file(self, *args, **kwargs):
        raise LocalOnly("file upload runs on the Meta Model API, and "
                        "this tenant reads on its own hardware")

    def transcribe(self, *args, **kwargs):
        raise LocalOnly("voice transcription runs on the Meta Model "
                        "API, and this tenant reads on its own hardware")

    def image_edit(self, *args, **kwargs):
        raise LocalOnly("Muse Image runs on the Meta Model API, and "
                        "this tenant reads on its own hardware")


def db_bound_local_client() -> LocalClient:
    """A local client bound to the eyewitness_meta_* tables."""
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

    return LocalClient(log_call=_log, cache_get=_get, cache_put=_put)

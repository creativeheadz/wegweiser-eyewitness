"""Evidence template catalogue: every template is a data file.

One YAML file per template sits beside this module. Operator files
in EYEWITNESS_CATALOGUE_DIR may add vision templates only. A bad
built-in file raises CatalogueError at import; a bad operator file
is skipped with one log line. Web and worker both need a restart
to see a change.
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,39}$")
_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
_CHOICE_RE = re.compile(r"^[a-z0-9_]{1,40}$")
_READERS = ("vision", "screen", "sticker", "photo", "video", "voice", "beeps")
_PHOTO_READERS = ("vision", "screen", "sticker", "photo")
_VISION_RETAKE = ("not_found", "glare", "blur", "cropped", "too_far", "other")
_ENVELOPE = frozenset({"found", "legible", "retake_reason", "text_lines",
                       "observations", "fields", "instruction_like_text",
                       "instruction_excerpt"})
_KEYS = frozenset({"id", "version", "kind", "title", "instruction",
                   "max_seconds", "next_time", "ask_when", "keywords",
                   "reader", "record_label", "look_for", "label",
                   "fields", "retake"})
_FORBID = ("<", ">", "{", "}")


class CatalogueError(ValueError):
    pass


def _err(fname: str, rule: str) -> CatalogueError:
    return CatalogueError(f"{fname}: {rule}")


def _text_ok(fname, key, value, lo, hi, *, one_line=True):
    if not isinstance(value, str):
        raise _err(fname, f"{key} must be text")
    if not (lo <= len(value) <= hi):
        raise _err(fname, f"{key} must be {lo} to {hi} characters")
    if one_line and ("\n" in value or "\r" in value):
        raise _err(fname, f"{key} must be one line")


def _customer_text(fname, key, value, lo, hi):
    _text_ok(fname, key, value, lo, hi)
    for ch in _FORBID:
        if ch in value:
            raise _err(fname, f"{key} may not contain {ch!r}")
    if "http://" in value.lower() or "https://" in value.lower() or "www." in value.lower():
        raise _err(fname, f"{key} may not contain a URL")


def _validate(data, fname: str, *, operator: bool, question_names: frozenset) -> dict:
    if not isinstance(data, dict):
        raise _err(fname, "file must hold one mapping")
    unknown = set(data) - _KEYS
    if unknown:
        raise _err(fname, f"unknown key {sorted(unknown)[0]}")
    tid = data.get("id")
    if not isinstance(tid, str) or not _ID_RE.match(tid):
        raise _err(fname, "id must match ^[a-z][a-z0-9_]{2,39}$")
    if os.path.splitext(os.path.basename(fname))[0] != tid:
        raise _err(fname, "id must equal the file name")
    if tid == "custom":
        raise _err(fname, "id custom is refused")
    if tid in question_names:
        raise _err(fname, "id must not be a question name")
    if tid.startswith("q_"):
        raise _err(fname, "id must not start with q_")
    ver = data.get("version")
    if not isinstance(ver, int) or isinstance(ver, bool) or ver < 1:
        raise _err(fname, "version must be an integer from 1")
    kind = data.get("kind")
    if kind not in ("photo", "video", "voice"):
        raise _err(fname, "kind must be photo, video or voice")
    _customer_text(fname, "title", data.get("title"), 3, 80)
    _customer_text(fname, "instruction", data.get("instruction"), 10, 300)
    ms = data.get("max_seconds")
    if kind in ("video", "voice"):
        if not isinstance(ms, int) or isinstance(ms, bool) or not (3 <= ms <= 60 if kind == "video" else 5 <= ms <= 60):
            raise _err(fname, "max_seconds is required in range for video and voice")
    else:
        if ms is not None:
            raise _err(fname, "max_seconds must be absent or null for photo")
    nt = data.get("next_time", False)
    if not isinstance(nt, bool):
        raise _err(fname, "next_time must be a boolean")
    _text_ok(fname, "ask_when", data.get("ask_when"), 10, 300)
    kw = data.get("keywords")
    if kw is not None:
        if not isinstance(kw, list) or len(kw) > 12:
            raise _err(fname, "keywords must hold at most 12 phrases")
        for w in kw:
            if not isinstance(w, str) or not (3 <= len(w) <= 40) or w != w.lower():
                raise _err(fname, "each keyword must be a lowercase phrase of 3 to 40 characters")
    reader = data.get("reader")
    if reader not in _READERS:
        raise _err(fname, "reader must be one of vision, screen, sticker, photo, video, voice, beeps")
    if operator and reader != "vision":
        raise _err(fname, "an operator file may only use reader vision")
    if reader in _PHOTO_READERS and kind != "photo":
        raise _err(fname, f"reader {reader} needs kind photo")
    if reader == "video" and kind != "video":
        raise _err(fname, "reader video needs kind video")
    if reader in ("voice", "beeps") and kind != "voice":
        raise _err(fname, f"reader {reader} needs kind voice")
    rl = data.get("record_label")
    if rl is not None:
        if kind != "voice":
            raise _err(fname, "record_label is only for kind voice")
        _customer_text(fname, "record_label", rl, 1, 30)
    retake = data.get("retake")
    if reader == "vision":
        # A prompt may run over several lines, so a YAML block is fine.
        _text_ok(fname, "look_for", data.get("look_for"), 40, 1500,
                 one_line=False)
        _text_ok(fname, "label", data.get("label"), 2, 40, one_line=True)
        fields = data.get("fields")
        if not isinstance(fields, list) or not (1 <= len(fields) <= 8):
            raise _err(fname, "fields must hold 1 to 8 fields")
        names = set()
        for f in fields:
            if not isinstance(f, dict):
                raise _err(fname, "each field must be a mapping")
            extra = set(f) - {"name", "type", "describe", "choices", "in_summary"}
            if extra:
                raise _err(fname, f"unknown field key {sorted(extra)[0]}")
            nm = f.get("name")
            if not isinstance(nm, str) or not _FIELD_RE.match(nm):
                raise _err(fname, "field name must match ^[a-z][a-z0-9_]{1,30}$")
            if nm in _ENVELOPE:
                raise _err(fname, f"field name {nm} is an envelope name")
            if nm in names:
                raise _err(fname, f"field {nm} is used twice")
            names.add(nm)
            tp = f.get("type")
            if tp not in ("text", "choice", "list", "yes_no"):
                raise _err(fname, f"field {nm} type must be text, choice, list or yes_no")
            _text_ok(fname, f"field {nm} describe", f.get("describe"), 5, 200)
            ch = f.get("choices")
            if tp == "choice":
                if not isinstance(ch, list) or not (2 <= len(ch) <= 16):
                    raise _err(fname, f"field {nm} needs 2 to 16 choices")
            if ch is not None:
                if tp in ("text", "yes_no"):
                    raise _err(fname, f"field {nm} of type {tp} takes no choices")
                if not isinstance(ch, list) or len(ch) > 16:
                    raise _err(fname, f"field {nm} choices must hold at most 16 values")
                for c in ch:
                    if not isinstance(c, str) or not _CHOICE_RE.match(c):
                        raise _err(fname, f"field {nm} choice {c!r} must match ^[a-z0-9_]{{1,40}}$")
            if "in_summary" in f and not isinstance(f["in_summary"], bool):
                raise _err(fname, f"field {nm} in_summary must be a boolean")
        if not isinstance(retake, dict) or "not_found" not in retake:
            raise _err(fname, "retake needs not_found for reader vision")
        for k, v in retake.items():
            if k not in _VISION_RETAKE:
                raise _err(fname, f"retake key {k} is not allowed for reader vision")
            _customer_text(fname, f"retake {k}", v, 10, 200)
    elif reader == "beeps":
        if retake is not None:
            if not isinstance(retake, dict):
                raise _err(fname, "retake must be a mapping")
            for k, v in retake.items():
                if k not in ("too_short", "too_quiet", "no_beeps", "unreadable"):
                    raise _err(fname, f"retake key {k} is not allowed for reader beeps")
                _customer_text(fname, f"retake {k}", v, 10, 200)
        for k in ("look_for", "label", "fields"):
            if k in data:
                raise _err(fname, f"{k} is only for reader vision")
    else:
        if retake is not None:
            raise _err(fname, f"retake is refused for reader {reader}")
        for k in ("look_for", "label", "fields"):
            if k in data:
                raise _err(fname, f"{k} is only for reader vision")
    spec = dict(data)
    spec.setdefault("next_time", False)
    spec.setdefault("keywords", None)
    spec.setdefault("record_label", None)
    spec.setdefault("retake", None)
    # Normalise fields into plain dicts with defaults.
    if spec.get("fields"):
        norm = []
        for f in spec["fields"]:
            norm.append({"name": f["name"], "type": f["type"],
                         "describe": f["describe"],
                         "choices": list(f["choices"]) if f.get("choices") else None,
                         "in_summary": bool(f.get("in_summary", False))})
        spec["fields"] = norm
    return spec


def _load_dir(folder: str, *, operator: bool, question_names: frozenset,
              taken: set, out: dict) -> None:
    import yaml
    if not os.path.isdir(folder):
        return
    for name in sorted(os.listdir(folder)):
        if not name.endswith((".yaml", ".yml")):
            continue
        path = os.path.join(folder, name)
        try:
            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            spec = _validate(data, name, operator=operator,
                             question_names=question_names)
            if spec["id"] in taken:
                raise _err(name, f"id {spec['id']} is already used")
        except CatalogueError as exc:
            if operator:
                logger.warning("eyewitness catalogue: skipped %s (%s)", name, exc)
                continue
            raise
        except Exception as exc:  # unreadable or bad yaml
            if operator:
                logger.warning("eyewitness catalogue: skipped %s (%s)", name, exc)
                continue
            raise _err(name, str(exc)[:200])
        taken.add(spec["id"])
        out[spec["id"]] = spec


def _question_names() -> frozenset:
    return frozenset({"since_when", "how_often", "what_changed"})


def _built_in_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def load_catalogue() -> dict:
    out: dict = {}
    taken: set = set()
    _load_dir(_built_in_dir(), operator=False,
              question_names=_question_names(), taken=taken, out=out)
    op_dir = os.environ.get("EYEWITNESS_CATALOGUE_DIR") or ""
    if op_dir:
        _load_dir(op_dir, operator=True, question_names=_question_names(),
                  taken=taken, out=out)
    return out


CATALOGUE: dict = load_catalogue()


def get(template_id: str) -> dict | None:
    return CATALOGUE.get(template_id)


def all_specs() -> list[dict]:
    """Specs in file name order, the catalogue order used everywhere."""
    return [CATALOGUE[k] for k in sorted(CATALOGUE)]


def built_in_ids() -> frozenset:
    """The ids of the files shipped beside this module."""
    return frozenset(os.path.splitext(n)[0]
                     for n in os.listdir(_built_in_dir())
                     if n.endswith((".yaml", ".yml")))


def is_built_in_path(path: str) -> bool:
    """True when `path` is the built-in folder or a file in it."""
    real = os.path.realpath(path)
    folder = real if os.path.isdir(real) else os.path.dirname(real)
    return folder == os.path.realpath(_built_in_dir())


def check_paths(entries: list) -> list[tuple[str, dict | None, str | None]]:
    """Validate (path, operator) pairs for catalogue-check.

    Returns one (name, spec, error) row per file, in order. Operator
    files are held to the operator rules, as at load: vision only,
    and no id a built-in file already uses.
    """
    import yaml
    rows: list[tuple[str, dict | None, str | None]] = []
    qn = _question_names()
    shipped = built_in_ids()
    for p, operator in entries:
        files = []
        if os.path.isdir(p):
            files = [os.path.join(p, n) for n in sorted(os.listdir(p))
                     if n.endswith((".yaml", ".yml"))]
        elif os.path.isfile(p):
            files = [p]
        else:
            rows.append((str(p), None, f"{p}: file or folder not found"))
            continue
        for path in files:
            name = os.path.basename(path)
            try:
                with open(path, encoding="utf-8") as fh:
                    data = yaml.safe_load(fh)
                spec = _validate(data, name, operator=operator,
                                 question_names=qn)
                if operator and spec["id"] in shipped:
                    raise _err(name, f"id {spec['id']} is already used")
                rows.append((name, spec, None))
            except CatalogueError as exc:
                rows.append((name, None, str(exc)))
            except Exception as exc:
                rows.append((name, None, f"{name}: {str(exc)[:200]}"))
    return rows

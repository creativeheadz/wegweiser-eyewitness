"""Sticker reader helpers (7.3): serial normalisation and matching.

Barcode-first reading lands with the real reader in Phase 2; the match
itself is pure and tested now. Placeholder serials never match.
"""
from __future__ import annotations

import re

_PLACEHOLDERS = {"", "none", "unknown", "n/a", "na", "tbd",
                 "serialnumber", "systemserialnumber", "0123456789",
                 "xxxxxxxx", "defaultstring", "tobe filled by o.e.m.",
                 "to be filled by o.e.m."}


def real_serial(value) -> str | None:
    """A serial worth comparing, or None for placeholders/empties."""
    s = re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()
    placeholders = {re.sub(r"[^a-z0-9]", "", p) for p in _PLACEHOLDERS}
    if not s or s.lower() in placeholders or len(s) < 4:
        return None
    if len(set(s)) == 1:  # XXXX..., 0000... (house rule in rmm_links)
        return None
    return s


def serial_match(read_value, known_values) -> str:
    """match | mismatch | unknown against the ticket device's serials."""
    got = real_serial(read_value)
    if got is None:
        return "unknown"
    known = {real_serial(v) for v in (known_values or [])}
    known.discard(None)
    if not known:
        return "unknown"
    return "match" if got in known else "mismatch"

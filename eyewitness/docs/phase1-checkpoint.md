# Phase 1 checkpoint - Eyewitness (2026-10-28, local)

Run overrides applied: commit, never push; no SSH; local checkpoint
instead of the staging checkpoint; total Meta spend under $1.00.

## What was built

- **Spikes** (`eyewitness/tools/spike.py`, fixtures in
  `eyewitness/tools/fixtures/spike-results.json`): synthetic BSOD +
  sticker images (Pillow + QR), a 10 s amber×2/white×3 blink MP4
  (Pillow frames + ffmpeg), all generated locally. No real photos.
- **Package** `app/eyewitness/`: models (7.1 + per-tenant settings),
  Meta client (own key, cache, daily cap, cost log, one retry),
  SAM parser fitted to the measured wire format, prices, connectors
  (Zammad implemented; Zendesk/Freshdesk/HaloPSA/ConnectWise stubs),
  templates + approval rules, intake validation + hold/release
  decisions, security (outgoing filter, untrusted fence, sniffing),
  readers (quality gate, screen stub with zxing QR decode, sticker
  serial match, blink grouping + Dell decode, voice/beep stubs),
  summary builder, Celery tasks, prompts, Dell LED table.
- **Routes**: public `/eyewitness/<token>` page with ordered items,
  upload → reader stub → polled verdict, skip, quick answers,
  all-skipped voice offer, Done with next-time stay-open; technician
  panel at `/helpdesk/tickets/<id>/evidence` with manual request,
  link + QR, filter hits and Meta spend. Captures under
  `EYEWITNESS_STORAGE_DIR`, UUID names, SHA-256 recorded.
- **Migration** `eyewitness_20261027` on head
  `exe_audit_indexes_20261024`, idempotent creates.
- **Glue** (listed in `docs/integration.md`): `app/__init__.py`
  blueprint + CSRF exemption for public POSTs only, ticket template
  mount point, `requirements.txt`, ROADMAP entry, `.gitignore`.

## Spike results (real API, synthetic inputs)

| Spike | Result | Tokens / units | USD |
|---|---|---|---|
| Spark screen read | ok: `bsod`, stop code `MEMORY_MANAGEMENT (0x1A)`, legible | 1664 in / 276 out | $0.00325 |
| Spark intake | ok: screen + light video + sticker, 2 questions | 209 in / 561 out | $0.00265 |
| SAM 3.1 image | ok: QR code boxed + masked (parser verified on the SSE) | 1 image ×2 calls | $0.0125 |
| SAM 3.1 video | HTTP 200 stream, **0 objects** on the synthetic clip - unverified | 50-frame floor | $0.0100 |
| Muse Image | ok: image returned | 1 image | $0.0100 |
| Voice Transcribe | **skipped**: no local TTS, file endpoint unconfirmed | - | $0 |
| Search grounding | **skipped**: no confirmed parameter, no spend | - | $0 |

**Total estimated Meta spend: $0.0384 of the $1.00 cap.**
Spark needs strict JSON schemas (every property in `required`);
SAM answers SSE whose final text is `<0f>N<|box;…|><|mask;…|>` -
the parser handles that measured format.

## Local verification (observed)

- `pytest tests/eyewitness -q` - **14 passed**.
- Touched-area suites: `test_templates_parse`, `test_no_inline_styles`,
  `test_link_colour` - 5 passed; `test_csp` - 12 passed;
  `test_helpdesk` - 15 passed, 42 skipped (pre-existing skips).
- Blueprint imports (11 routes); `compileall` clean; customer/expired
  templates parse; `node --check` clean on both JS files. The panel
  fragment uses the app's `timestamp_to_datetime` filter, so it only
  parses inside the app (the house template test covers it).
- Migration checked statically: revision chain on the current head,
  six idempotent table guards, model/migration table parity.
  Alembic offline SQL was not run: the house migrations inspect a
  live bind, so offline rendering is not available for them.
- Reader stub offline: zxing decoded the synthetic BSOD QR;
  a dark 100px image returns `retake` with no model call.

## Not yet done / honest gaps

- Intake hook, governor handover and summary posting are Phase 2;
  `run_intake`/`release_ticket` tasks are placeholders by design.
- `attach_file` posts the article without bytes (Phase 2).
- Customer page uses `capture` file inputs (the 7.2 fallback);
  live `getUserMedia` preview/countdown is not built yet.
- SAM video tracking returned no objects on the synthetic clip.
- Voice and search-grounding spikes did not run (above).

## Still needs verifying on staging (Claude Code / Andrei)

1. `venv/bin/pip install -r requirements.txt` (adds opencv, zxing),
   then `FLASK_APP=wsgi.py venv/bin/flask db upgrade`.
2. Eyewitness env drop-ins + `EYEWITNESS_META_API_KEY` present
   (reported only as configured / not configured).
3. Link the staging tenant to the staging Zammad in the UI; enable
   Eyewitness for that tenant.
4. Manual request on a Brenda ticket: public note appears in Zammad,
   link opens on a real phone, photo uploads, reading shows in the
   ticket view, QR scans.
5. SAM 3.1 on a real blinking-light video; voice note end to end.
6. Full suite `venv/bin/python -m pytest tests/` before any push
   (the run overrides reserve pushing for review).

## Next

Phase 2: intake hook + Spark intake + hold/release, real screen and
sticker readers, internal summary + governor handover, all of 7.8
enforced end to end, injection fixture demo.

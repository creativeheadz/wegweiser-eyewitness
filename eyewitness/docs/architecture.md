# Eyewitness architecture

Pipeline: inbound ticket to intake agent (Spark, structured output,
templates only) to evidence request (token link, ordered items) to
customer phone captures to readers with QA (quality gate, Spark,
SAM 3.1 plus deterministic blink decode, Voice Transcribe) to
internal summary note to existing governor (`ticket_triage.py`) to
case with captures attached. Hold and release: Eyewitness holds the
governor only for enabled tenants on desk and help origins, and
releases once on Done, expiry, manual release, or the max hold
timeout. A capture that lands after release on a next-time item
posts a follow-up internal note and the governor does not run again.

Data: `eyewitness_intake_runs`, `eyewitness_requests`,
`eyewitness_captures`, `eyewitness_meta_calls`,
`eyewitness_meta_cache`, `eyewitness_tenant_settings`
(migrations `eyewitness_20261027` and
`eyewitness_retention_20261028`). Captures live under
`EYEWITNESS_STORAGE_DIR` with UUID names and a SHA-256, never under
`app/static`.

Readers (Phase 3). Video: ffmpeg transcodes every upload to H.264
MP4 at 10 fps with faststart, keeping at most 20 seconds. SAM 3.1
gets one concept prompt. Where that finds nothing, code finds the
candidate that changes once the frames are lined up, and SAM follows
it from a point prompt on the frame where it is brightest. Code then
measures the region on every frame with hysteresis, groups the
flashes and decodes them with `decode/dell_led.json`. Where SAM
follows nothing the region stays the code's, and the reading names
the locator. Three keyframes go to Spark in one
call for what visibly happens. Beeps come from the audio track by
an energy envelope and `decode/beep_codes.json` (S3, no model).
Voice: ffmpeg makes 16 kHz mono WAV with a 60 second cap, one batch
transcribe call follows, and one Spark summary with
`voice_summary_v2` names the language and writes in English. Every
reader retake is a reason code from a fixed list with a fixed
sentence, on video and voice as on photos. Guide photos (S2) are
technician triggered only: Muse Image marks an earlier photo and
the result joins a new request that waits for approval.

Phone page: live camera preview with a canvas photo capture, a
3-2-1 countdown and an automatic stop at the item limit for video,
and a timer for voice. The file input stays where the camera is
refused or missing. Client and server checks hold the same numbers
under one test each.

The help form sent page (S1) polls `GET /get-help/<token>/evidence`
in `app/eyewitness/routes.py`, authorised by the help token. It
returns only that ticket's open evidence link and its QR image,
rendered on the server with the `qrcode` library.

Retention: a daily beat sweep deletes capture files
`EYEWITNESS_RETENTION_DAYS` (default 90) after the ticket close,
or after the request expiry where the close is unknown, and stamps
`purged_at` on the request and capture rows. The date rule is a
pure function in `app/eyewitness/retention.py`.

Security (brief 7.8): no free model text to customers; custom items
and second requests need technician approval; the outgoing filter
runs even after approval; readings are fenced as untrusted for the
summary and the governor; readers flag instruction-like text;
uploads are sniffed server-side; tokens are 256-bit, scoped,
expiring and rate limited; EXIF and GPS stripped; retention sweep;
never contributor models for customer evidence.

Cost: one Meta client with cache, per-call USD log and a daily cap
(`EYEWITNESS_DAILY_USD_CAP`, default $5). The ticket view figure is
the sum of the `MetaCall` rows for the ticket's runs and captures,
held against the call log by a test. The key is Eyewitness's own
and deliberately bypasses the tenant AI config and the
non-production guard; no key means paid steps are skipped and
tickets flow to the governor unchanged.

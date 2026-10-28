# Eyewitness architecture

Pipeline: inbound ticket → intake agent (Spark, structured output,
templates only) → evidence request (token link, ordered items) →
customer phone captures → readers with QA (quality gate, Spark,
SAM 3.1 + deterministic blink decode, Voice Transcribe) → internal
summary note → existing governor (`ticket_triage.py`) → case with
captures attached. Hold/release: Eyewitness holds the governor only
for enabled tenants on desk/help origins, releases exactly once on
Done, expiry, manual release, or the max-hold timeout.

Data: `eyewitness_intake_runs`, `eyewitness_requests`,
`eyewitness_captures`, `eyewitness_meta_calls`,
`eyewitness_meta_cache`, `eyewitness_tenant_settings`
(migration `eyewitness_20261027`). Captures live under
`EYEWITNESS_STORAGE_DIR` with UUID names + SHA-256, never under
`app/static`.

Security (brief 7.8): no free model text to customers; custom items
and second requests need technician approval; the outgoing filter
runs even after approval; readings are fenced as untrusted for the
summary and the governor; readers flag instruction-like text;
uploads are sniffed server-side; tokens are 256-bit, scoped,
expiring, rate limited; EXIF/GPS stripped; retention sweep;
never contributor models for customer evidence.

Cost: one Meta client with cache, per-call USD log and a daily cap
(`EYEWITNESS_DAILY_USD_CAP`, default $5). The key is Eyewitness's
own and deliberately bypasses the tenant AI config and the
non-production guard; no key means paid steps are skipped and
tickets flow to the governor unchanged.

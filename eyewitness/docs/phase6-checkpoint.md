# Phase 6 checkpoint - Eyewitness (2026-10-29, local)

Run overrides applied: commit, never push; no SSH to any host; no staging access. Claude Code reviews, pushes, deploys, applies the migration on staging, connects staging to the local server, and runs the `local` scenario.

## What was built

- **The local client.** `LocalClient` in `app/eyewitness/meta/local.py` carries the `spark_json` contract of `MetaClient`. It reads `EYEWITNESS_LOCAL_BASE_URL` (required before it counts as configured), `EYEWITNESS_LOCAL_MODEL` (default `eyewitness-glimmer`), `EYEWITNESS_LOCAL_API_KEY` (a placeholder goes out when empty), `EYEWITNESS_LOCAL_TIMEOUT_S` (default 600) and `EYEWITNESS_LOCAL_EXTRA_BODY` (JSON, default `{"reasoning_effort": "none"}`). Requests are chat completions with a strict JSON schema, `max_tokens` 1024 and temperature 0.2. Answers parse through `raw_decode`, so the stray `<|eot|>` Glimmer appends does no harm. The parsed object is checked against the schema's required keys and types before any reader sees it. One retry covers a connection error or a 5xx. A 4xx gets none. Cache and call log are the Meta client's tables: kind `local`, the configured model name, token counts from `usage`, the measured milliseconds, and $0 on every row. Saved reads return at no time. The client has no SAM, no file upload, no transcription and no image edit. Those calls raise `LocalOnly`, and `supports_sam` and `supports_transcribe` read False where `MetaClient` reads True.
- **The tenant choice.** Migration `eyewitness_onprem_20261029.py` adds `reader_backend` to `eyewitness_tenant_settings`: a string, not null, server default `meta`, values `meta` or `local`. The model carries the same column. `reader_client(tenant_uuid)` in `meta/client.py` returns the local client for a tenant set to `local` and the Meta client for everyone else. Absence of a row means `meta`.
- **Every call site routed.** The intake agent (`intake.assess_run`), the Evidence API's automatic choice (`api_routes.api_create_request` into `api.decide_auto`), the technician guide route, and the four readers through `tasks.process_capture`, which looks the tenant up from the request and passes the client in. Each reader also takes a `tenant_uuid` and resolves through `reader_client` when called without a client. The remaining `db_bound_client` calls in the app are its definition and the `reader_client` fallback itself. `eyewitness/tools/spike.py` names `db_bound_client` once to check it exists. The spike is a developer tool with no tenant and no customer evidence, so it stays as it was.
- **The light video on local.** With a client that has `supports_sam` False, no SAM call and no upload happens. `locate_by_motion` finds the light, code counts, and three keyframes go to Glimmer for the description. The Meta path keeps its SAM steps untouched.
- **Voice notes on local.** A client without transcription support returns a reading that says the note is kept for the technician to listen to and that no transcription was made. The file stays where uploads already keep it. The panel and `eyewitness/docs/onprem.md` say the same.
- **Guide photos on local.** The guide route takes `reader_client`. `LocalClient.image_edit` raises `LocalOnly`, the guide reader's existing fallback draws the mark on this server with Pillow, and the panel names that next to the form.
- **What people see.** Ticket settings gained the choice "Meta Model API" or "Your own hardware (Muse Glimmer)" in the Eyewitness connections panel, saved through `POST /eyewitness/settings/reader-backend`. The local choice is disabled with a short reason while `EYEWITNESS_LOCAL_BASE_URL` is unset, and one line under it says photos and videos then stay on the tenant's network, reads take a minute or two, and voice notes are not transcribed. `GET /eyewitness/settings.json` carries the current backend, whether a local server is set up, and the local model name. The technician panel shows each reading's provenance from the call log ("Read on your hardware by Muse Glimmer in 1 min 18 s", or a saved read), with helpers in `summary.py`. The internal summary gains one line outside the untrusted fence in both builders: "Readings were made on your hardware by Muse Glimmer." The Meta line names the Meta Model API the same way. Local reads hold the Meta spend line at $0.0000 because their rows cost $0. The customer page carries `data-read-wait` at 600 seconds for a local tenant where a Meta tenant carries 300, and shows one fixed sentence from the template while a local read runs. `capture.js` reads both from the page.

## The no-fallback rule and how it is held

For a tenant set to `local`, the client object itself is the boundary. Call sites receive a `LocalClient`, which has no code path to the Meta Model API: SAM, upload, transcription and image edit raise `LocalOnly` in place. When the local server is down, `spark_json` raises after one retry, and each reader takes the degraded path it already had. The screen and sticker readers keep the photo with a "photo received" reading. The intake records the run as failed and releases the ticket. `test_no_fallback_when_the_local_server_is_down` puts a guard on every `MetaClient` method, runs the intake with its database reads stood in for, runs each reader against a local client whose transport refuses every call, and asserts the guard never fired.

## Local verification (observed)

- `venv/bin/python -m pytest tests/eyewitness -q` - **116 passed**. The 22 new Phase 6 tests in `tests/eyewitness/test_phase6_local.py` cover the request payload (content list, schema, extra body from the environment), the `<|eot|>` parse, missing-key and wrong-type schema refusals, retry on 5xx and on connection errors with none on a 4xx, the saved-read cache at $0 and no time, the `supports_*` flags with `LocalOnly` on cloud-only calls, backend selection per tenant, the no-fallback rule above, the video path without SAM (amber 2, white 3 on a generated clip, locator `code`, Dell meaning decoded), the voice path without transcription, the guide drawn on the server, the settings form and its endpoint refusing `local` with no server set up, the summary line outside the fence with the $0.0000 cost line, the panel provenance wording, the phone wait attribute, and the migration's shape.
- Full Wegweiser suite (`venv/bin/python -m pytest tests -q -p no:cacheprovider`): **20 failed, 4491 passed, 894 skipped**. The 20 failures sit in the six baseline files named in the brief: `test_analysis_policy.py` (11), `test_briefing.py` (2), `test_eventlog_upload.py` (1), `test_integration_hardening.py` (1), `test_score_breakdown.py` (1) and `test_security_report_pdf.py` (4). No other file failed.
- `ruff check --select F401,F821` on the new and touched modules: one finding in the new test file, fixed. Four findings predate this phase and stay untouched: `app/eyewitness/readers/screen.py`, two in `app/eyewitness/routes.py`, and one in `eyewitness/tools/e2e_staging.py`.
- `eyewitness/tools/e2e_staging.py --help` lists `local` among its scenarios. The scenario itself needs staging and has not run yet.
- No live Meta call ran in this session, so Meta spend for this run is $0.

## Live local reads (observed, this machine)

Run outside pytest against Ollama at `http://127.0.0.1:11434/v1` with `EYEWITNESS_LOCAL_BASE_URL` set, one read at a time, through `LocalClient` and the production readers:

- **Screen.** `Hackathon/golden/windows11-crash-screen-0x1A.png`: **77.9 seconds**, verdict ok. Stop code `MEMORY_MANAGEMENT (0x1A)`, kind bsod, legible. Every line came back, down to "87% complete". The call log row recorded kind `local`, model `eyewitness-glimmer`, 1,870 input tokens, 130 output tokens, 77,817 ms, $0.
- **Video.** `Hackathon/synthetic/synthetic-blinking-dell-memory.mp4`: **50.1 seconds** end to end, with the keyframe description at 48.9 seconds inside it. Verdict ok, locator `code` (no SAM call, no upload). Bursts counted amber 2, white 3 and repeated with the clip. The Dell table gave "Memory not detected / no memory installed". Glimmer's keyframe description named the power light turning white then amber. The call log row recorded kind `local`, 2,552 input tokens, 63 output tokens, $0.

## Staging steps for Claude Code

```bash
# 1. Push the branch. Staging pulls feature/eyewitness each minute.
git push origin feature/eyewitness

# 2. Migrate on staging (the pull does not run migrations).
ssh <staging host> 'cd <app folder> && FLASK_APP=wsgi.py venv/bin/flask db upgrade'

# 3. Point staging at the local server. The app and Celery services
#    both need the variables, then a restart of each.
#    EYEWITNESS_LOCAL_BASE_URL=http://127.0.0.1:11434/v1
#    EYEWITNESS_LOCAL_MODEL=eyewitness-glimmer

# 4. Make one API key if the api scenario has none yet, and keep the
#    printed value for the local run.
ssh <staging host> 'cd <app folder> && venv/bin/flask create-api-key --tenant <tenant uuid> --name "Staging e2e"'

# 5. Run the local scenario from a checkout with the staging rows.
#    It sets the demo tenant to local, uploads the golden photo, waits
#    up to ten minutes, checks the stop code with $0 and the local
#    model name in the call log, then sets the tenant back to meta.
EYEWITNESS_API_KEY=<printed key> venv/bin/python eyewitness/tools/e2e_staging.py --scenario local --tenant <tenant uuid> --golden <golden crash-screen png> --base-url <staging base url>
```

Ticket settings shows the local choice once the variable is set. Picking it there is the same switch the scenario flips in the database, so a manual phone check can follow the scenario without a database edit.

## Gaps carried forward

- The migration has not run on staging, and the `local` scenario still needs its first staging run.
- Staging still needs `EYEWITNESS_LOCAL_BASE_URL` on both services before the local choice opens in Ticket settings.
- The phone wait and the fixed sentence were checked in the template and the script here. A staging phone check belongs with the scenario run.

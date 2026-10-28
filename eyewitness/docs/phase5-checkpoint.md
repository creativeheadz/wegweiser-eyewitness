# Phase 5 checkpoint - Eyewitness (2026-10-29, local)

Run overrides applied: commit, never push; no SSH to any host; no staging access. Claude Code reviews, pushes, deploys and runs the staging scenarios.

## What was built

- **Zammad push webhook.** `POST /eyewitness/hooks/zammad/<hook id>` in `app/eyewitness/api_routes.py`, with one hook id per tenant on the tenant settings row. The HMAC secret sits in the secret store under `eyewitness_webhook_secret:<tenant uuid>`, through the same get and set calls that hold the Zammad token for `TenantIntegration`. The endpoint checks `X-Hub-Signature: sha1=<hex>` over the raw body in constant time, caps the body at 64 KB, carries a rate limit, and is CSRF-exempt. The handler is `ingest_ticket` in `app/utilities/helpdesk_inbound.py`. It fetches the one ticket, runs `upsert`, and hands a new ticket over through `hand_over_new_ticket`, the helper the poll now uses as well. A ticket that is already indexed answers 200 and changes nothing.
- **Webhook setup.** `setup_zammad_webhook` in `app/eyewitness/webhook.py` creates the Zammad webhook first, then a ticket created trigger that points at its id, with the tenant secret as the signature token. Field names follow the Zammad API reference (`http_method`, `signature_token`, `notification.webhook`). The shell command is `flask setup-zammad-webhook`. Its admin token arrives at run time and is never stored or printed. `flask show-hook` prints the hook address, which also sits in Ticket settings.
- **Evidence API v1.** Keys live in `eyewitness_api_keys` (`eyewitness_api_20261029.py`). A key prints once at creation, and the table keeps a SHA-256 hash plus a visible prefix, with scopes `evidence:write` and `evidence:read`, `last_used_at` and `revoked_at`. Endpoints sit under `/api/eyewitness/v1/`: list templates, create a request from templates or from `{"auto": true, "ticket_text": ...}`, read one request, release, and cancel. Creation returns template-rendered `customer_text`, the link and the expiry. Readings return with `untrusted: true`, their injection flags, and the `customer_evidence` fence. Every endpoint carries a rate limit, and a key reads its own tenant alone.
- **Callbacks.** A key may carry one callback address. Events are `evidence.read`, `evidence.completed` and `evidence.expired`, fired from the capture reader, from release, and from the expiry sweep. Each call signs `<timestamp>.<body>` with HMAC-SHA256 in `X-Eyewitness-Signature` (`t=...,v1=...`), checks its address through the SSRF guard where it connects, and retries from Celery with growing waits. Delivery rows keep attempts and the last error.
- **Settings.** Ticket settings gained an Eyewitness connections panel. It shows the hook address, lists keys by prefix and scope, and creates a key through a CSRF-kept form that prints the new key once. Key creation also works from `flask create-api-key`, and `flask revoke-api-key` revokes one.
- **MCP server.** `eyewitness/mcp/server.py` runs on the `mcp` Python SDK (`mcp>=1.0,<2` in its own `requirements.txt`; Wegweiser requirements stay untouched). Five tools wrap the API: `list_templates`, `request_evidence`, `get_evidence`, `release_evidence` and `cancel_evidence`. It reads `EYEWITNESS_API_URL` and `EYEWITNESS_API_KEY`, speaks stdio and streamable HTTP, and returns readings inside the untrusted fence with flags in view.
- **Spark agent demo.** `eyewitness/mcp/spark_agent_demo.py` bridges the five tools as functions for Muse Spark function calling. It requests evidence from ticket wording, polls for readings, then asks Spark for a technician summary with the fence kept around the readings.
- **Docs and scenarios.** `eyewitness/docs/openapi.yaml` holds the OpenAPI 3.1 contract. `eyewitness/docs/api.md` holds curl examples plus the per desk setup notes for Zammad, Zendesk, Freshdesk, HaloPSA and ConnectWise. The README gained its Integrations section. `e2e_staging.py` gained `--scenario webhook` and `--scenario api`.

## Local verification (observed)

- `venv/bin/python -m pytest tests/eyewitness -q` - **84 passed**. The 18 new Phase 5 tests cover signature checks with computed digests, ticket id shapes, the setup calls over a fake admin transport, key hashes and scopes, tenant isolation, callback signing with its replay window, SSRF refusals for loopback and metadata addresses, auto template choices with negative cases, fenced serialising, the webhook and API routes through the Flask test client with computed signatures, the MCP client over a fake HTTP layer, the MCP tool schemas, and the Spark demo dispatch with a fake chat.
- Full Wegweiser suite (`venv/bin/python -m pytest tests -q -p no:cacheprovider`): **20 failed, 4459 passed, 894 skipped**. The 20 failures sit in the six baseline files and predate Eyewitness: `test_analysis_policy.py`, `test_briefing.py`, `test_eventlog_upload.py`, `test_integration_hardening.py`, `test_score_breakdown.py` and `test_security_report_pdf.py`. No other file failed.
- `ruff check --select F401,F821` on the new modules - clean. Three older findings in `app/eyewitness/routes.py` and `app/eyewitness/readers/screen.py` predate this phase and stay untouched here.
- The MCP server answered a stdio `list_tools` call with the five tool names and their schemas. A streamable HTTP start on 127.0.0.1 answered `initialize` with 200 and the server name `eyewitness`.
- The OpenAPI file parses as OpenAPI 3.1 with the six paths above.
- `e2e_staging.py --help` lists `webhook` and `api` among its scenarios.
- No live Meta call ran in this session, so Meta spend for this run is $0. The Spark demo was tested with fakes only.
- The webhook, the API and the MCP tools were tested with the test client and fakes. No ticket was pushed through a staging desk, and no callback reached a staging address.

## Security notes

- Customer text from the API comes from the template renderer in `app/eyewitness/requests.py`. No model wording reaches a customer through the new routes, and auto mode may only name templates after the intake schema check.
- Webhook and callback secrets sit in the secret store. API key plaintext and callback secrets print once at creation. The Zammad admin token is read at run time and held in memory for two calls.
- The webhook needs its signature before it reads a ticket. A bad signature answers 401, an unknown hook 404, and a body over the cap 413.
- Callback addresses pass `assert_safe_url` and the guarded connection, so private, loopback and metadata targets are refused at connect time.
- Readings keep the fence on every way out: the internal note, the governor, the API reply, and the MCP tools.

## Staging steps for Claude Code

```bash
# 1. Push the branch. Staging pulls feature/eyewitness each minute.
git push origin feature/eyewitness

# 2. Migrate on staging (the pull does not run migrations).
ssh <staging host> 'cd <app folder> && FLASK_APP=wsgi.py venv/bin/flask db upgrade'

# 3. Make the Zammad webhook and trigger. The admin token is typed
#    at run time and never stored.
ssh <staging host> 'cd <app folder> && ZAMMAD_ADMIN_TOKEN=<admin token> venv/bin/flask setup-zammad-webhook --tenant <tenant uuid> --base-url <staging base url>'
ssh <staging host> 'cd <app folder> && venv/bin/flask show-hook --tenant <tenant uuid> --base-url <staging base url>'

# 4. Make one API key and keep the printed value for the api run.
ssh <staging host> 'cd <app folder> && venv/bin/flask create-api-key --tenant <tenant uuid> --name "Staging e2e"'

# 5. Run both new scenarios from a checkout with the staging rows.
venv/bin/python eyewitness/tools/e2e_staging.py --scenario webhook --tenant <tenant uuid> --golden <golden crash-screen png> --base-url <staging base url>
EYEWITNESS_API_KEY=<printed key> venv/bin/python eyewitness/tools/e2e_staging.py --scenario api --tenant <tenant uuid> --base-url <staging base url>

# 6. MCP server and Spark demo, from this checkout.
venv/bin/pip install -r eyewitness/mcp/requirements.txt
EYEWITNESS_API_URL=<staging base url> EYEWITNESS_API_KEY=<printed key> venv/bin/python -m eyewitness.mcp.server --transport stdio
EYEWITNESS_API_URL=<staging base url> EYEWITNESS_API_KEY=<printed key> EYEWITNESS_META_API_KEY=<meta key> venv/bin/python -m eyewitness.mcp.spark_agent_demo --ticket-text "PC keeps crashing with a blue screen, now it blinks"
```

## Gaps carried forward

- The staging Zammad webhook, trigger and API key still need creating, with the commands below.
- The `webhook` and `api` staging scenarios still need their first staging run.
- The Spark demo still needs its first live run, which spends from the Meta key in its environment.

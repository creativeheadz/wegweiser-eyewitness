# Integration points (brief 6.2 glue)

Every edit outside `app/eyewitness/`, `app/templates/eyewitness/`,
`app/static/{js,css}/eyewitness/`, `tests/eyewitness/` and
`eyewitness/` is listed here, with its phase.

| File | Change | Phase |
|---|---|---|
| `app/__init__.py` | Import eyewitness models (Alembic), register `eyewitness_bp`, CSRF-exempt only the token-authenticated public POSTs | 1 |
| `app/templates/helpdesk/ticket.html` | Eyewitness mount div + `panel.js` script tag | 1 |
| `requirements.txt` | `opencv-python-headless`, `zxing-cpp` | 1 |
| `documentation/ROADMAP.md` | One dated In-progress entry | 1 |
| `.gitignore` | `data/eyewitness/` (brief 6.3 storage) | 1 |
| `app/utilities/helpdesk_inbound.py` | `on_new_ticket` hook before queueing the governor | 2 |
| `app/utilities/help_intake.py` | Same hook on the help-link submit path | 2 |
| `app/utilities/ticket_triage.py` | Fenced Eyewitness summary in the governor read; captures to case | 2 |
| `app/celery_app.py` | Import Eyewitness tasks beside the helpdesk tasks; five-minute expiry beat; daily retention beat | 2, 3 |
| `app/routes/helpdesk/intake.py` | The sent page carries the help token so its polling script can ask for the evidence link (S1) | 3 |
| `app/templates/helpdesk/help_form.html` | The phone QR section and polling script on the sent page (S1) | 3 |
| `app/utilities/csp.py` | `media-src 'self' blob:` and `blob:` in `img-src` for the live camera previews | 3 |
| `app/translations/de/LC_MESSAGES/messages.po` | Two S1 strings from the sanctioned catalogue update, with German text filled in | 3 |
| `app/__init__.py` | Register `eyewitness_api_bp`, CSRF-exempt the signed webhook and keyed API views, register the Phase 5 shell commands, inject the hook address for Ticket settings | 5 |
| `app/utilities/helpdesk_inbound.py` | `hand_over_new_ticket` shared by the poll and the push; `ingest_ticket` for one pushed ticket | 5 |
| `app/celery_app.py` | Import the callback delivery task beside the Eyewitness tasks | 5 |
| `migrations/versions/eyewitness_api_20261029.py` | API keys, callback deliveries, webhook hook id, API request columns | 5 |
| `app/templates/helpdesk/settings.html` | Eyewitness connections panel: hook address, key list, key creation form | 5 |
| `app/templates/helpdesk/settings.html` | The reader choice in the Eyewitness connections panel: the Meta Model API or Muse Glimmer on the tenant's own hardware | 6 |
| `migrations/versions/eyewitness_onprem_20261029.py` | The tenant's `reader_backend` column | 6 |
| `app/__init__.py` | Register the `catalogue-check` shell command beside the Phase 5 commands | 7 |

No changes to `case_engine.py` or the case models, ever (brief 6.2).

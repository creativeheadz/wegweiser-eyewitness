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

No changes to `case_engine.py` or the case models, ever (brief 6.2).

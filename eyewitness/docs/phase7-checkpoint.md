# Phase 7 checkpoint - Eyewitness (2026-10-29, local)

Run overrides applied: commit, never push; no SSH to any host; no staging access. Claude Code reviews, pushes, restarts the staging services, and runs the catalogue scenario.

## What was built

- **The catalogue.** `app/eyewitness/catalogue/` holds the loader in `__init__.py` and one YAML file per template, read with `yaml.safe_load` in file name order at import. Twelve files ship: the eight existing templates plus `printer_test_page`, `display_fault`, `network_lights` and `beep_recording`. Unknown keys are refused. Every rule from the brief is checked in code: the id shape and its bans, the version, kind and `max_seconds` together, reader and kind pairs, field names against the envelope, choice lists, vision and beeps retake keys, and the ban on angle brackets, braces and URLs in customer text. A bad built-in file raises `CatalogueError` naming the file and the rule. A folder in `EYEWITNESS_CATALOGUE_DIR` is read after it. Operator files may only use `reader: vision`, may not reuse a built-in id, and a bad file is skipped with one log line. Web and worker both need a restart to see a change, and `docs/catalogue.md` says so.
- **The old shapes hold.** `requests.TEMPLATES` is built from the files with exactly the old shape, so every existing consumer works unchanged. A frozen copy of the eight entries sits in the new test file and matches. `tasks.TEMPLATE_READERS` comes from the files plus `custom: photo`. `SCREEN_TEMPLATES` is derived from reader `screen`. `printer_panel` keeps its title and instruction and moves from the screen reader to `vision`. One existing Phase 2 assertion named the old printer routing and now names the new one, with the reason in a comment. `make_item` adds `record_label`, `phone_advice` and `raw_audio: true` only where a template defines them or uses the beeps reader, so items for the existing templates are unchanged.
- **The vision reader.** `readers/vision.py` follows `screen.py`: the quality gate first with no model call on a failed gate, the client from `reader_client(tenant_uuid)` where none is passed, and the stub `ok` reading where the client is not configured or the call fails. The system prompt is the fixed `prompts/vision_read_v1.md` plus a heading for the item, its `look_for` and one line per field. The schema is strict at both levels, with every field required. The pure verdict step checks each field again in code, caps text at 120 characters, drops list values outside their choices, runs `text_lines` through `cap_ocr`, and keeps at most five observations. `found` false gives the `not_found` sentence from the file. `legible` false gives the sentence for the reason, or the code default. The summary is built in code from the label and the filled `in_summary` fields, capped at 300 characters. The reading holds `template`, `catalogue_version`, `prompt_version`, `found`, `legible`, `fields`, `observations`, `text_lines`, `quality`, `summary` and `instruction_excerpt`. An unreadable file returns a retake with no model call.
- **Routing.** `process_capture` sends `vision` to `read_vision` with the spec and `beeps` to `read_beeps`. A template with no spec any more falls back by capture kind. `summary._line` and the HTML builder add observations (at most three, inside the fence) for readings that hold them. The panel keeps showing `summary`.
- **The beep reader.** `read_beeps` writes the upload to a temporary folder, converts with ffmpeg to 16 kHz mono WAV (20 second cap, timeout, `-protocol_whitelist file`, no network), measures duration and RMS with the voice numbers, and removes the folder whatever happens. Retakes are `too_short`, `too_quiet`, `no_beeps` and `unreadable` with the template sentences. A good reading holds the count, `pattern`, `pattern_text`, `meaning` and `source`, with a summary such as `beeps: long x1, short x2` plus the table pointer where one matches. It calls no model, so it runs the same for a local tenant.
- **Speech never counts.** `decode_beeps` gained `require_tonal`, off by default so the light video path is unchanged. Each tone must put at least 0.45 of its energy in a narrow band around one peak between 300 Hz and 4 kHz. The numbers sit in the beep bar section below.
- **The phone page.** A voice item with `raw_audio` asks `getUserMedia` for audio with `echoCancellation`, `noiseSuppression` and `autoGainControl` all false. The voice button shows `item.record_label` where one is set. Too-short and too-quiet sentences come from `data-too-short` and `data-too-quiet` on the item where present and fall back to the old constants. The shared-constants test holds the voice numbers with the server and checks the new attributes.
- **Intake v2.** `prompts/intake_v2.md` adds the catalogue lines to the v1 rules. `intake_v1.md` is untouched. One builder, `intake.catalogue_text`, writes the catalogue text for the intake agent, `api.decide_auto` and `eval._intake_cost`. Each quick question gained an `ask_when`. `PHYSICAL` gained `print_quality` and `network`. `templates_payload` keeps every old key and adds `reader`, `ask_when` and, for vision templates, `fields`. `docs/openapi.yaml` and `docs/api.md` name the new keys. The wording fallback adds keyword matches in catalogue order after its old checks and before the `since_when` question, within the cap of four. The existing templates define no keywords, so their behaviour is unchanged.
- **Checking files.** `flask eyewitness catalogue-check [PATH]` prints one line per template (id, kind, reader, OK or the rule broken), with `--show` adding the generated vision prompt and schema. Exit code 1 on any error. The command is registered beside the Phase 5 commands in `app/__init__.py`, listed in `docs/integration.md`.
- **Synthetic media.** `eyewitness/tools/make_catalogue_media.py` renders a test page with vertical streaks and a faint cyan block, a monitor in its frame showing a No Signal HDMI 1 box on black, a router front with labelled lights (Power green, Internet red, Wi-Fi green, LAN off) and a beep recording (one long, two short) as WAV, WebM Opus and M4A AAC. It ran into the Hackathon synthetic folder outside the repo. Every image passed the quality gate. The manifest gained eight entries, with two negatives: the router photo as a `display_fault` (expected `not_found`) and the English voice note as a beep recording (expected `no_beeps`).
- **Evaluation.** `eval.py` knows `vision` and `beeps`, checks expected field values and list members, and checks the beep `pattern`. It ran over the golden and synthetic sets and the regenerated `docs/eval.md` is committed.
- **The staging scenario.** `e2e_staging.py --scenario catalogue --catalogue-media <folder>` creates one request through the Evidence API with the four new templates, checks the catalogue titles and instructions exactly, sends the voice note to the beep item first (expected `no_beeps` retake), then uploads each synthetic file through the customer page routes and checks the readings: test page defects include streaks; display symptom `no_signal_message` with No Signal in the message; router `warning_light` true; beeps pattern `long:1 short:2`.

## Tests (observed)

- `venv/bin/python -m pytest tests/eyewitness -q -p no:cacheprovider` - **156 passed**. The 38 new Phase 7 tests in `tests/eyewitness/test_phase7_catalogue.py` cover the shipped files, every refusal rule over files in a temporary folder, the frozen eight entries, the routing table, the operator folder, house rules over shipped customer text, the schema and prompt builders, each verdict branch with field sanitising and the summary, no model call on a failed quality gate, the no-fallback rule for `read_vision` and the model-free beep path, WAV positives and negatives, the phone attributes with the script constraints, the intake text, the shared builder in `decide_auto`, the wording fallback, `templates_payload`, and `catalogue-check` exit codes.
- Full Wegweiser suite (`venv/bin/python -m pytest tests -q -p no:cacheprovider`): **20 failed, 4531 passed, 894 skipped**. The 20 failures sit in the six baseline files: `test_analysis_policy.py`, `test_briefing.py`, `test_eventlog_upload.py`, `test_integration_hardening.py`, `test_score_breakdown.py` and `test_security_report_pdf.py`. No other file failed.
- Evaluation run: golden **1/1 passed**, synthetic **16/16 passed**, 19 calls for $0.1306. The cache kept nothing from earlier runs in the fresh demo folder, so this run paid in full.

## The beep bar (measured)

`_tonal_ratio` on synthetic segments at 16 kHz: sine 880 Hz at 1.000, sine 2000 Hz at 1.000, square 1000 Hz at 0.814, square 700 Hz at 0.811, a speech-like harmonic stack at 0.128, white noise at 0.047, silence at 0.000. The bar at 0.45 sits between 0.128 and 0.811, with a margin on both sides. A full decode of one long and two short tones returned `long:1 short:2`. The speech-like and noise decodes returned no beeps.

## Live reads (observed, outside pytest)

Meta reads through `read_vision` with the production client:

- `printer_test_page` on its synthetic photo: **5.2 seconds**, verdict ok. Defects `['streaks']`, model `ColourJet CJ-410 (synthetic)`, toner levels and page count read as printed. The faint cyan block was not flagged as a missing colour. Summary: `test page; defects: streaks; ColourJet CJ-410 (synthetic)`.
- `display_fault` on its synthetic photo: **2.9 seconds**, verdict ok. Symptom `no_signal_message`, message `No Signal HDMI 1`, input `HDMI 1`, model `ViewPanel VP-240 (synthetic)`.
- `network_lights` on its synthetic photo: **1.9 seconds**, verdict ok. Lights Power green, Internet red, Wi-Fi green, LAN off. `warning_light` true, model `RouteBox RB-120 (synthetic)`.
- `printer_panel` on the router photo, where no panel shows: **2.3 seconds**, verdict retake with reason `not_found` and the template sentence.

Local read, run alone through `LocalClient` on Ollama here with `EYEWITNESS_LOCAL_BASE_URL=http://127.0.0.1:11434/v1`:

- `display_fault` on its synthetic photo: **98.8 seconds**, verdict ok. Symptom `no_signal_message`, message `No Signal HDMI 1`, input `HDMI 1`, model `ViewPanel VP-240 (synthetic)`. The call log row recorded kind `local`, model `eyewitness-glimmer`, $0.

## Live intake checks (observed, Meta, EYEWITNESS_CACHE=off)

Brenda's demo ticket (Black screen, now it just blinks; the black screen twice this morning with tiny writing at the bottom; now it will not start and the power light blinks), three runs with Device: unknown:

- Run 1, 3.4 seconds: `power_light_video`, `screen_photo_next_time`, question `what_changed`.
- Run 2, 4.2 seconds: `power_light_video`, `screen_photo_next_time`, `sticker`, question `what_changed`.
- Run 3, 2.2 seconds: `power_light_video`, `screen_photo_next_time`, question `what_changed`.

Each run asked for the two items the v1 demo asked for, with the `what_changed` question. No `ask_when` line needed a change.

Once each:

- Streaky printing: `printer_test_page` with `sticker`, physical `print_quality`.
- A monitor that says no signal: `display_fault` with `cables_back`, physical `display_error`.
- The internet down with a red light on the router: `network_lights`, physical `network`.
- A PC that beeps three times and will not start: `beep_recording` with `sticker`, physical `beeps`.
- A password reset: nothing asked, physical `none`.

Eight intake calls for $0.0273.

Meta spend for this run: vision reads $0.0194, intake checks $0.0273, evaluation $0.1306. Total **$0.1773**, under the $0.50 cap.

## Staging steps for Claude Code

```bash
# 1. Push the branch. Staging pulls feature/eyewitness each minute.
git push origin feature/eyewitness

# 2. No migration and no new package in this phase. PyYAML is
#    already pinned and installed. Restart web and worker so both
#    load the catalogue files.
ssh <staging host> 'systemctl restart wegweiser.service wegweiser-celery.service'

# 3. Check the shipped files on staging.
ssh <staging host> 'cd <app folder> && FLASK_APP=wsgi.py venv/bin/flask eyewitness catalogue-check'

# 4. Make one API key if the api scenario has none yet, and keep
#    the printed value for the catalogue run.
ssh <staging host> 'cd <app folder> && venv/bin/flask create-api-key --tenant <tenant uuid> --name "Staging e2e"'

# 5. Run the catalogue scenario. The media folder is the one
#    make_catalogue_media.py wrote, with the voice note file in it.
EYEWITNESS_API_KEY=<printed key> venv/bin/python eyewitness/tools/e2e_staging.py --scenario catalogue --catalogue-media <folder> --base-url <staging base url> --tenant <tenant uuid>
```

## Gaps carried forward

- The `catalogue` scenario still needs its first staging run. It needs staging deployed with the restart above, an Evidence API key, and the synthetic media folder beside the checkout that runs it.
- `printer_panel` has no synthetic panel photo of its own. Its live Meta check ran on the router photo and returned the `not_found` retake, which is the wording a wrong photo gets. A panel photo would complete the set.
- The evaluation demo folder started empty in this run, so the run paid $0.1306 in full. A second run over the same files reads the cache at no cost.

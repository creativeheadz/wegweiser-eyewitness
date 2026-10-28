# Phase 2 checkpoint - Eyewitness (2026-10-28, local)

Run overrides applied: commit, never push; no SSH; local checkpoint in
place of the staging checkpoint; total Meta spend under $2.00 for this
run.

## What was built

- **Screen reader v2** (`app/eyewitness/prompts/screen_read_v2.md`,
  `app/eyewitness/readers/screen.py`): the current Windows 11 crash
  screen is the first case. That screen is black, with white text and
  the stop code in small text at the bottom. A QR code is optional in
  the prompt and in the verdict. A dark frame goes to Spark for a
  legibility judgement, because the black screen is dark across the
  whole photo. Size, blur and glare still stop at the quality gate
  with no model call. Where Spark finds the screen illegible and the
  stop code missing, the retake advice is fixed wording: "Move closer
  so the small line at the bottom of the screen fills more of the
  photo". Prompt v1 stays untouched on disk.
- **Sticker reader** (`app/eyewitness/readers/sticker.py`): barcode
  first, then one Spark read. The serial check compares against the
  ticket device serials. A mismatch returns a retake. A Dell label
  adds the Dell support link for the service tag.
- **Intake agent and hold/release** (`app/eyewitness/intake.py`,
  `app/eyewitness/tasks.py`): one Spark call with a strict schema.
  Output passes through the Phase 1 validator before anything is
  built from it. A bound device keeps its sticker item only where its
  serial is missing. The request goes out through the Zammad
  connector, or waits where the approval rules say it waits. Release
  posts one internal summary note and queues the governor once, under
  an atomic claim on the run row. Release triggers cover Done, a
  completed request with the voice offer settled, link expiry, a
  technician pressing Release now, and the 24 hour safety timeout.
  The beat sweep runs every five minutes.
- **Hooks**: the inbound poll and the help-link submit path call
  `on_new_ticket` first. The governor read carries the fenced
  Eyewitness block. Where the governor opens a case by itself, the
  best finished photo captures travel through the existing case
  attachment path. No case file changed.
- **Panel and page**: the ticket panel sends or cancels a request
  that waits for approval, and releases a held ticket on request. The
  customer page asks the quick questions and posts each answer through
  the token answer route. A voice offer the customer accepts adds the
  voice item and reloads the page.
- **Security follow-through**: every public token POST carries the 30
  per hour rate limit. Technician downloads send
  `X-Content-Type-Options: nosniff`. Two pure tests guard the run:
  one parses `create_app` and fails on a plain `import app...` line;
  one scans the customer-facing templates and wording modules for em
  and en dashes and for the banned crutch words.

## Golden image result

One live Spark call on `windows11-crash-screen-0x1A.png` from the
golden set, held outside the repo:

| Check | Result |
|---|---|
| Verdict | ok |
| Kind | bsod |
| Stop code | MEMORY_MANAGEMENT (0x1A) |
| Error codes | 0x1A |
| QR code | none found, none required |
| Legible | true |
| Injection flag | false |

Cost: $0.0036 (2,022 input tokens, 252 output tokens). The full
reading is recorded in `eyewitness/tools/fixtures/golden-screen-v2.json`.
Total Meta spend for this run: $0.0036 of the $2.00 cap. The golden
image at first failed the dark-photo quality gate at a mean brightness
of 1.75. That failure is the reason dark frames now go to Spark.

## Local verification (observed)

- `pytest tests/eyewitness -q` - **23 passed** (14 Phase 1 tests, 7
  Phase 2 reader and completion tests, 2 guard tests).
- Touched-area suites (`test_helpdesk`, `test_templates_parse`,
  `test_no_inline_styles`, `test_link_colour`,
  `test_house_style_words`) - 27 passed, 42 skipped. The skips match
  the Phase 1 baseline.
- `test_house_style_dashes` fails on this branch. It fails on the
  untouched Phase 1 tree as well, on dashes in Phase 1 files. This run
  added no new dash to that count.
- `compileall` clean on every touched module. `node --check` clean on
  both customer and panel scripts.
- The Meta client reads only `EYEWITNESS_META_API_KEY`. No Eyewitness
  module reads the tenant AI config, so the staging guard that swaps
  paid providers for a local model does not touch Eyewitness calls.
  The governor still runs on the local model on staging.

## What Claude Code must verify on staging

1. Push `feature/eyewitness`, then run the migration and requirements
   steps from the brief where they changed. This phase adds no
   migration and no requirement.
2. Confirm `EYEWITNESS_META_API_KEY` reads as configured in the app
   environment. Report it as configured or not configured only.
3. Enable Eyewitness for the demo tenant in the staging UI, or let
   the script below set that one row.
4. Run the end-to-end script inside the app environment on staging:

   `venv/bin/python eyewitness/tools/e2e_staging.py --tenant <tenant uuid> --golden <path to the golden PNG> --base-url <staging base URL>`

   It files a Brenda ticket, polls it in, plays the customer over the
   public routes with the golden photo, and prints one PASS or FAIL
   line per step.
5. Open the evidence link on a phone. Check the quick questions, the
   skip path and the voice offer with the screen in hand.
6. Run `venv/bin/python -m pytest tests/` before the push that follows
   this review. The run overrides reserve pushing for review.

## Gaps carried forward

- Video light reading stays a stub in this phase. It lands in Phase 3
  with SAM tracking and the blink decode.
- Voice transcription stays a stub in this phase. It lands in Phase 3.
- The help-form QR code (S1) stays Phase 3. The help-link hold itself
  is wired in this phase.
- Retention sweep stays a stub. It lands in Phase 3.

## Next

Phase 3: video lights with SAM 3.1, voice notes, the skip and
next-time paths in full, the help-button ticket path on a phone, and
cost on the ticket view checked against the call log.

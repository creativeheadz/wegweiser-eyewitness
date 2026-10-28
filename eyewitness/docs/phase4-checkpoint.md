# Phase 4 checkpoint - Eyewitness (2026-10-28, local)

Run overrides applied: commit, never push; no SSH to any host; total Meta spend under $3.00 for this run. Claude Code reviews, pushes, deploys and runs the staging scenarios.

## What was built

- **Evaluation** (`eyewitness/tools/eval.py`, `eyewitness/docs/eval.md`): each reader runs over the golden set and over a synthetic set whose manifest sits beside the files with each entry marked synthetic. Accuracy stays apart per set and per reader. Cost for one ticket adds the intake read to one screen photo, one light video and one voice note. The Meta cache sits in a file in the demo folder, so a second run over the same files costs nothing.
- **Phone page, one step at a time** (`app/templates/eyewitness/customer.html`, `app/static/js/eyewitness/capture.js`, `app/static/css/eyewitness/customer.css`): with script the page shows one item with Step 1 of 3 wording and Back and Next buttons. With script off the full list stays as the fallback. Buttons meet a 48 px touch target. Progress and verdict lines carry live regions for assistive tools. The page shows the tenant logo from `Tenants.logo_path` where one sits on the tenant, and the tenant name at other times.
- **Small fixes**: verdict polling stops after five minutes with a fixed sentence. A file uploaded for SAM carries `expires_after` for one hour with `purpose` sent before the file part, and code deletes the file once the SAM calls end. The open days wording and the Done wording read `EYEWITNESS_NEXT_TIME_DAYS`. The manual request and the guide request name the tenant in the customer wording, as the intake path does.
- **Demo kit** (`eyewitness/demo/`): `storyboard.yaml` holds the eleven approved scenes with wording word for word. `panels.py` with `panels.yaml` draws six Muse Image panels with no text inside the pictures. `overlays.py` draws the SAM box with the one-bit mask inside it where the parser decodes one, plus a timeline that fills as the clip plays, the crash screen with its stop-code line boxed, and the sticky note with its treated as data panel. The SAM stream comes from one point prompt on the candidate the code locator finds, and the raw stream sits in the demo folder for reuse. `record_phone.py` records the phone page at 390 by 844 with a fake camera fed from synthetic media. `record_desktop.py` holds no password; its login step opens a visible browser for Andrei to sign in himself and saves the storage state at mode 600. `build_video.py` makes 1920 by 1080 at 30 frames per second with loudnorm, trimmed silences, crossfades, burned captions, corner badges, a title over the first scene and the GitHub address on the end card. It fails past 2:58. Its vertical flag adds the 1080 by 1920 cut for scenes 1, 2, 5, 7, 9 and 11. Captions come through Voice Transcribe where voice files sit in the demo folder, and from the storyboard wording at other times.
- **Draft video**: panels, overlays and phone recordings were generated. Desktop scenes show a plain card that names the scene, since those recordings need a sign-in. The draft voice comes from ffmpeg flite, and each voice file name marks it as a placeholder.

## Evaluation results

Golden set: one capture, the Windows 11 crash screen from a demo virtual machine. Synthetic set: eight files made on this machine. Wording for the table comes from the run that wrote `eyewitness/docs/eval.md`.

| Set | Reader | Items | Passed | Cost on that run |
|---|---|---|---|---|
| Golden | screen | 1 | 1 | $0.0029 |
| Synthetic | screen | 1 | 1 | $0.0022 |
| Synthetic | video | 3 | 3 | $0.0959 |
| Synthetic | voice | 3 | 3 | $0.0062 |
| Synthetic | photo | 1 | 1 | $0.0000 |

Detail from that run: the crash screen read `MEMORY_MANAGEMENT (0x1A)`. Each blink clip counted amber 2, white 3 in both MP4 and WebM form. The shaken clip with no light returned the light not found reason. Each voice file returned a transcript that begins with the front desk greeting. The sticky note photo named the same stop code and set the flag for text that looks like instructions to an AI.

Cost for one ticket on that run:

| Part | Cost |
|---|---|
| Intake read | $0.0029 |
| Screen photo | $0.0029 |
| Light video | $0.0280 |
| Voice note | $0.0020 |
| One ticket in total | $0.0357 |

## Draft video

- Horizontal cut: `demo-media/draft/eyewitness-draft.mp4` in the demo folder outside the repo, 165 seconds at 1920 by 1080 and 30 frames per second. The storyboard sums to 170 seconds; crossfades take five seconds from the joins.
- Vertical cut: `demo-media/draft/eyewitness-draft-vertical.mp4`, 90.5 seconds at 1080 by 1920 for scenes 1, 2, 5, 7, 9 and 11.

Scene timings from the storyboard:

| Scene | Start | Length | Visual in the draft |
|---|---|---|---|
| 1 | 0:00 | 10 s | Comic panel, title over it |
| 2 | 0:10 | 12 s | Comic panel |
| 3 | 0:22 | 14 s | Placeholder card for the desktop recording |
| 4 | 0:36 | 19 s | Placeholder card for the desktop recording |
| 5 | 0:55 | 23 s | Phone recording, step 1 |
| 6 | 1:18 | 14 s | Phone recording, step 2 with countdown |
| 7 | 1:32 | 20 s | Blink clip with SAM box, mask where decoded, and timeline |
| 8 | 1:52 | 14 s | Crash screen with boxed stop-code line and voice card |
| 9 | 2:06 | 18 s | Sticky note photo with treated as data panel |
| 10 | 2:24 | 16 s | Placeholder card for the desktop recording |
| 11 | 2:40 | 10 s | Comic panel with end card and GitHub address |

Phone page checks at 390 by 844 and at 360 by 740 sit in the demo folder as `customer-390x844.png`, `customer-390x844-step2.png`, `customer-360x740.png`, `customer-360x740-step2.png` and `customer-390x844-nojs.png`. The first view at each size shows Step 1 of 3 with one item and a 48 px Next button. The view with script off shows the full list.

## Cost for this run

Live calls from the call logs in this session: the evaluation runs, six panel generations at one cent, and one SAM point prompt for the overlay. The second evaluation run read the cache in full and cost nothing. The total sits near $0.33 of the $3.00 cap. No key value was printed or written.

## Local verification (observed)

- `pytest tests/eyewitness -q` - **57 passed** (47 from Phases 1 to 3, 10 new for Phase 4), with no database and no paid calls in the tests. The SAM file tests use a fake HTTP layer for both id shapes.
- `node --check` clean on the customer script. `compileall` clean on each touched module and on each demo script.
- Full Wegweiser suite (`pytest tests -q -p no:cacheprovider`): **20 failed, 4432 passed, 894 skipped**. The 20 failures sit in the six baseline files and predate Eyewitness: `test_analysis_policy.py`, `test_briefing.py`, `test_eventlog_upload.py`, `test_integration_hardening.py`, `test_score_breakdown.py` and `test_security_report_pdf.py`. No other file failed.
- Playwright checks for the phone page ran against a local Flask test app with fixture rows. Screenshots at both sizes were opened and read. The overlay frame and three draft frames were opened and read as well.

## Gaps carried forward

- Desktop scenes 3, 4 and 10 stay as cards in the draft until the sign-in recordings exist.
- The draft voice stays as flite placeholder files until the recorded voice files sit in the demo folder.
- The golden set still holds one capture. Further captures from physical machines will widen the screen and light scores.

## Fix pass 1 (2026-10-28)

- **Phone steps.** The stepper moves to the next unfinished item after a Got it verdict, after a skip, and after an answer. Every step change focuses the new heading. The customer page route passes each item state from its latest capture through `item_done_states` in `app/eyewitness/intake.py`, the template marks a finished item with `data-done`, and the stepper starts at the first unfinished item. Accepting the voice offer reloads the page onto the new voice step. The plain list stays in the markup for script off. Pure tests hold the state helper (`tests/eyewitness/test_phase4_pure.py`).
- **Phone screenshots.** Playwright at 390 by 844 against a local fixture app wrote `customer-390x844-resume.png`, `customer-390x844-advance.png` and `customer-390x844-nojs-resume.png` into the demo folder. The resume view opens at Step 2 of 3 with its heading focused. A skip on that step opens Step 3 of 3 with its heading focused. The script off view shows the full list.
- **Panels.** Muse Image takes a 1920 by 1080 size and returns 2048 by 1152. The six panels were regenerated at that size with no text inside the pictures. Panels now fill the 16:9 frame in the horizontal cut.
- **Captions.** Phone scenes place the phone at the left with the caption beside it in the horizontal cut. The vertical cut places the phone at the top with the caption below it. The end card address sits above the caption with a clear gap. Its colon is escaped for the drawtext parser, so the full GitHub address shows.
- **Scene 8 language.** The voice card takes its transcript and its language from the voice reading (`eyewitness/demo/overlays.py --voice`). For the draft note the reading is in English, so the card says English. The build prints `WARNING: scene 8 caption says the voice note was recorded in German, but the voice reading is in English.` The approved voice-over wording stays unchanged for Andrei to decide.
- **Phone recorder.** `record_phone.py` drops `--no-sandbox`. One recording with the fake camera fed from synthetic media showed the synthetic PC in the preview and uploaded a photo, so Chrome runs this with its sandbox on.
- **Rebuilt draft.** Horizontal cut: `demo-media/draft/eyewitness-draft.mp4` in the demo folder outside the repo, 165 seconds at 1920 by 1080 and 30 frames per second. Vertical cut: `demo-media/draft/eyewitness-draft-vertical.mp4`, 90.5 seconds at 1080 by 1920. One frame from every scene in the horizontal cut and one from every scene in the vertical cut were opened and read. Scene 5 and scene 6 captions sit beside the phone. The scene 11 address shows in full in both cuts.
- **Tests.** `pytest tests/eyewitness -q` gives 64 passed. The full suite gives 20 failed, 4439 passed and 894 skipped. The 20 failures sit in the six baseline files named above.
- **Spend.** Two probe generations cost $0.02. Six panel generations cost $0.06. One voice reading for the scene 8 card cost $0.0022, and its repeat read the cache. The total for this pass sits near $0.08 of the $0.50 cap. No key value was printed or written.
- **For Andrei.** The scene 8 voice-over says the note was recorded in German. The note in the draft is in English, the card says English, and the build warning names the mismatch. A decision on that wording is still needed.

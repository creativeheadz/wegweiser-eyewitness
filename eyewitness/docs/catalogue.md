# The evidence catalogue

What a customer can be asked to capture is described in data files,
not in code. Each template is one YAML file in
`app/eyewitness/catalogue/`, read with `yaml.safe_load` in file name
order when the app starts. Adding a photo template that a model
reads is a new file. Counting, timing and thresholds stay in code
readers, whose tables are data (`decode/beep_codes.json`,
`decode/dell_led.json`). A model can describe a photo and fill
fields. It cannot hear beeps, and it miscounts flashes, so those
stay in code.

Web and worker both need a restart to see a changed file.

## What a file holds

One mapping per file. The keys are fixed, and any other key is
refused at load, so a typo fails loudly.

| Key | Rule |
|---|---|
| `id` | Equals the file name without `.yaml`. Matches `^[a-z][a-z0-9_]{2,39}$`. Never `custom`, never a quick question name, never starting `q_`. |
| `version` | Integer from 1. The reading records it as `template@version`. |
| `kind` | `photo`, `video` or `voice`. A voice item records sound from the phone microphone. |
| `title` | Customer text, 3 to 80 characters, one line. |
| `instruction` | Customer text, 10 to 300 characters, one line. |
| `max_seconds` | Required for `video` (3 to 60) and `voice` (5 to 60). Absent or null for `photo`. |
| `next_time` | Boolean, default false. True lets the item stay open under the next time wording. |
| `ask_when` | 10 to 300 characters, one line. It tells the intake model when to ask for this template. |
| `keywords` | Optional. Up to 12 lowercase phrases of 3 to 40 characters. The wording fallback uses them when no model answers. |
| `reader` | `vision`, `screen`, `sticker`, `photo`, `video`, `voice` or `beeps`. |
| `record_label` | Optional, kind `voice` only, at most 30 characters. The phone button shows it, with Record voice note as the default. |
| `look_for` | Vision only. 40 to 1500 characters. What the model looks for in the photo. |
| `label` | Vision only. 2 to 40 characters. The start of the reading summary. |
| `fields` | Vision only. 1 to 8 fields the model fills. Code checks each one again. |
| `retake` | Reason to fixed customer sentence, 10 to 200 characters each. The wording for each reader is set out below. |

Customer text (`title`, `instruction`, `record_label` and every
`retake` sentence) may not hold `<`, `>`, `{`, `}` or a URL. No model
text reaches a customer. A reader returns a reason, and the customer
sees the fixed sentence for that reason.

Reader and kind go together. `vision`, `screen`, `sticker` and
`photo` need kind `photo`. `video` needs kind `video`. `voice` and
`beeps` need kind `voice`.

For `vision`, `retake` needs `not_found`. `glare`, `blur`,
`cropped`, `too_far` and `other` may be added. For `beeps`,
`too_short`, `too_quiet`, `no_beeps` and `unreadable` are all
optional, with defaults in code. Every other reader refuses
`retake`. Its advice stays in code, unchanged.

## A field

Each field in `fields` holds:

| Key | Rule |
|---|---|
| `name` | Matches `^[a-z][a-z0-9_]{1,30}$`. Never an envelope name (`found`, `legible`, `retake_reason`, `text_lines`, `observations`, `fields`, `instruction_like_text`, `instruction_excerpt`). |
| `type` | `text`, `choice`, `list` or `yes_no`. |
| `describe` | 5 to 200 characters. Required. It tells the model what to fill. |
| `choices` | 2 to 16 values matching `^[a-z0-9_]{1,40}$`. Required for `choice`, optional for `list`, refused for `text` and `yes_no`. |
| `in_summary` | Boolean, default false. True puts a filled value in the summary line code builds. |

## The readers

| Reader | What runs |
|---|---|
| `vision` | The generic reader in `readers/vision.py`. The quality gate runs first, with no model call on a failed gate. One structured read follows the fixed prompt `prompts/vision_read_v1.md`, with this item, its `look_for` and one line per field added by code. Code checks every field again, builds the summary from the label and the `in_summary` fields, and turns a reason into the template sentence. |
| `screen` | The Windows crash screen reader. Its prompt is written for that layout, so printer panels no longer use it. |
| `sticker` | Barcode first, then a model read, with the serial checked against the ticket device. |
| `photo` | The quality gate only. The technician reads the photo. |
| `video` | SAM tracking with code counting the flashes, plus the beep track from the clip audio. |
| `voice` | Transcription, then a model summary. |
| `beeps` | Code only, no model. The recording is converted to 16 kHz mono WAV with a 20 second cap. Duration and level use the voice numbers, so phone and server agree. Only tonal segments count, so speech never counts as beeps. The count, pattern and table pointer come from `decode/beep_codes.json`. It runs the same on a tenant reading on its own hardware. |

A new template needs a code reader only where the task itself is
code work: counting, timing, a threshold, a barcode or a serial
check. A photo that a model only has to describe and fill fields
for is a `vision` file.

## Writing a good look_for

- Describe what is visible. Name the parts of the photo and where
  they sit.
- Ask for text copied exactly, with codes kept as shown.
- Never ask the model to diagnose, to judge a cause or to advise.
  The technician decides.
- Leave counting, timing and thresholds to code. Ask for one entry
  per light, each with its label and colour, and let code hold the
  summary. Ask for every defect seen from a fixed list, and never
  for a count of flashes or beeps.
- Say what to use when the photo shows nothing: the schema fills
  null, an empty list or `none`, and the model must never guess.

## The operator folder

Set `EYEWITNESS_CATALOGUE_DIR` to a folder of extra files. It is
read after the built-in folder. Files there may only use
`reader: vision`. They may not reuse a built-in id. A bad file is
skipped with one log line naming the file and the rule broken,
where a bad built-in file stops the app at import with the same
wording. Web and worker both need a restart to see a change.

## Checking files

```bash
venv/bin/flask eyewitness catalogue-check [PATH]
```

With no path it checks the built-in folder, then the operator
folder where one is set. It prints one line per template with the
id, kind, reader and OK, or the rule broken. Exit code 1 on any
error. `--show` also prints the generated vision prompt and schema
for each vision template.

## A complete file

`printer_test_page.yaml`, as shipped:

```yaml
id: printer_test_page
version: 1
kind: photo
title: "Photograph a printed test page"
instruction: "Print a test page from the printer's own menu. Lay it flat in good light and photograph the whole page."
ask_when: "The ticket describes poor print quality: streaks, lines, faded or patchy print, smudges, missing colours or blank pages."
keywords: [streak, streaks, faded print, smudge, print quality, blank page, lines on the page, missing colour]
reader: vision
look_for: "A printed page, ideally the printer's own test or configuration page. Look for print defects: streaks or lines running down the page, bands across it, faded or patchy areas, smudges, a ghost of the image repeated lower down, a colour missing from the colour blocks, skew, or a blank page. Read the printer model, the page count and any toner or ink levels printed on the page."
label: "test page"
fields:
  - name: defects
    type: list
    describe: "Every print defect you can see. Use none when the print looks clean."
    choices: [streaks, bands, faded, patchy, smudges, ghosting, missing_colour, skewed, blank_page, none]
    in_summary: true
  - name: missing_colours
    type: list
    describe: "Colours that are missing or very faint in the colour blocks."
    choices: [cyan, magenta, yellow, black]
    in_summary: true
  - name: printer_model
    type: text
    describe: "The printer model printed on the page, exactly as printed, or null."
    in_summary: true
  - name: toner_levels
    type: text
    describe: "Toner or ink levels printed on the page, exactly as printed, or null."
  - name: page_count
    type: text
    describe: "The page count printed on the page, or null."
retake:
  not_found: "No printed page could be seen in the photo. Lay the page flat and photograph all of it."
  glare: "Light is reflecting off the page. Move away from the window or lamp and try again."
  cropped: "Part of the page is cut off. Step back so the whole page fits in the photo."
  blur: "The photo is out of focus. Hold the phone steady above the page and tap the screen to focus."
  other: "The page cannot be read. Lay it flat in good light and photograph all of it."
```

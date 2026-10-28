# Eyewitness reader evaluation

Date: 2026-10-28. The golden set holds real captures: screens and devices photographed or recorded from machines, physical or virtual. The synthetic set holds files made on this machine. The two sets stay apart in every table below.

Folders come from EYEWITNESS_GOLDEN_DIR and EYEWITNESS_SYNTHETIC_DIR. Paths stay out of this file, which is published.

## Golden set (real captures)

| Reader | Items | Passed | Cost |
|---|---|---|---|
| screen | 1 | 1 | $0.0029 |

| File | Reader | Result | Detail |
|---|---|---|---|
| windows11-crash-screen-0x1A.png | screen | pass | MEMORY_MANAGEMENT (0x1A) |

Golden cost for this run: $0.0029 across 1 items.

## Synthetic set (files made on this machine)

| Reader | Items | Passed | Cost |
|---|---|---|---|
| photo | 1 | 1 | $0.0000 |
| screen | 1 | 1 | $0.0022 |
| video | 3 | 3 | $0.0959 |
| voice | 3 | 3 | $0.0062 |

| File | Reader | Result | Detail |
|---|---|---|---|
| synthetic-blinking-dell-memory.mp4 | video | pass | amber:2 white:3 |
| synthetic-blinking-dell-memory.webm | video | pass | amber:2 white:3 |
| synthetic-no-blink-shake.mp4 | video | pass | light_not_found |
| synthetic-voice-english.webm | voice | pass | Hello, this is Brandu at the front desk. My computer crashed |
| synthetic-voice-english.m4a | voice | pass | Hello, this is Brandu at the front desk. My computer crashed |
| synthetic-voice-english.wav | voice | pass | Hello, this is Brandu at the front desk. My computer crashed |
| synthetic-injection-sticky.png | screen | pass | MEMORY_MANAGEMENT (0x1A) (flagged) |
| synthetic-pc-front.png | photo | pass | photo received |

Synthetic cost for this run: $0.1043 across 8 items.

## Cost for one ticket

One ticket here means the intake read plus one screen photo, one light video and one voice note. The parts below use the first measured cost for each reader. A repeat run reads the cache and costs nothing.

| Part | Cost |
|---|---|
| Intake read | $0.0029 |
| Screen photo | $0.0029 |
| Light video | $0.0280 |
| Voice note | $0.0020 |
| One ticket in total | $0.0357 |

This run logged 15 Meta calls (0 from the cache) for $0.1101 in total. The cache file sits in the demo folder and a second run over the same files adds no cost.

## How to read this

Each reader returns a verdict in code. The screen reader must name the stop code on the crash screen. The video reader must count two amber flashes and three white flashes on the blink clips, and must return the light not found reason on the shaken clip with no light. The voice reader must return a transcript. The sticky note photo must set the flag for text that looks like instructions to an AI.

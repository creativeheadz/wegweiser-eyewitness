# Wegweiser Eyewitness

**When the agent goes dark, the user's phone becomes the sensor.**

Eyewitness makes tickets good enough to act on, before anyone works
them. When a new helpdesk ticket arrives, an intake agent (Muse Spark)
decides what is missing and whether the problem shows itself
physically — a blue screen, a machine that won't boot, blinking
lights, a printer panel. If evidence would help, the customer gets a
mobile page with an ordered checklist. Every capture is checked, not
accepted blindly: Spark reads screens and stickers, SAM 3.1 tracks
blinking lights while code counts the flashes, and Muse Voice
Transcribe handles voice notes. Unusable captures get precise retake
advice. When the request completes, a structured internal note lands
on the ticket and the existing ticket governor starts from a ticket
with evidence attached.

This is not a dashboard or a simple image analyser: it is an agent
that decides what a ticket is missing, asks a human for exactly that,
checks what comes back, and hands a better ticket to the next agent.

## Which Meta model does what

| Model | Job | Price (2026-10-26) |
|---|---|---|
| `muse-spark-1.3` | Intake agent; screen, sticker and voice-summary reads | $1.25/$4.25 per 1M in/out |
| `sam-3.1` | Track indicator lights; find screens/stickers | $2.50/1k images, $0.20/1k frames (min 50) |
| `muse-voice-transcribe-1.0` | Voice notes, 25 languages | $0.18/hour |
| `muse-image-1.0` | Guide images on the customer's own photo (S2) | $0.01/image |

Measured Phase 1 cost: about $0.04 of spikes total; a Spark screen
read costs roughly $0.003. See `docs/phase1-checkpoint.md`.

## Layout

- `app/eyewitness/` — models, routes, tasks, intake, readers,
  connectors, Meta client, prompts (in the Wegweiser repo; see
  `docs/integration.md` for what the public repo cannot contain).
- `eyewitness/` (this folder) — README, docs, tools, demo kit.

## Security

Customer evidence is untrusted data: template-only customer text,
technician approval for anything custom, an outgoing filter that
blocks device identifiers and secrets, readings fenced as
`<customer_evidence untrusted="true">`, an injection flag on
instruction-like text, hardened uploads, expiring scoped tokens.
Full design: `docs/architecture.md`.

## Status

Built 26 Oct – 4 Nov 2026 for the Meta Global AI Developer
Hackathon. Phase 1 (spikes + skeleton) complete; intake hook and
real readers land in Phase 2, video/voice in Phase 3. Roadmap:
Zendesk, Freshdesk, HaloPSA and ConnectWise connectors (stubs with
API notes today), live video assist, on-prem readers, and
evidence-quality analytics.

# Readers on your own hardware (Phase 6)

Some support companies cannot let a customer's photos leave their own
network. A tenant can choose where its evidence is read:

- **Meta Model API.** Model calls go to Meta, as in Phases 1 to 5.
- **Your own hardware (Muse Glimmer).** Every model call for the
  tenant goes to Muse Glimmer on a server the support company runs
  itself. Muse Glimmer is Meta's open-weights model (27.9B, Apache
  2.0, text and image in). Any OpenAI-compatible server can host it:
  Ollama, llama.cpp's `llama-server`, vLLM, or a partner host.

The choice sits on the tenant settings row (`reader_backend`, values
`meta` or `local`) and is picked in Ticket settings under Eyewitness
connections. The second choice stays disabled until
`EYEWITNESS_LOCAL_BASE_URL` is set on the installation, because there
is nowhere to send the reads before that.

## What local mode does

- The intake agent, the screen reader, the sticker reader and the
  light description inside the video reader all call Glimmer through
  `LocalClient` (`app/eyewitness/meta/local.py`). It carries the same
  `spark_json` contract as the Meta client, with the same cache and
  the same call log. Log rows carry kind `local`, the configured
  model name, the measured time, token counts from the server, and a
  cost of $0.
- The light video keeps its counting in code. SAM runs only in the
  cloud, so the code locator (`locate_by_motion`) finds the light on
  its own and code counts the flashes, as it already did with no key
  configured. Three keyframes go to Glimmer for the description of
  what visibly happens. No clip is uploaded anywhere.
- Customer pages wait longer. A page for a local tenant carries
  `data-read-wait` at 600 seconds where a Meta tenant carries 300,
  and shows one fixed sentence while the read runs.

## What local mode does not do

- **No fallback to the cloud.** For a tenant set to `local`, no
  customer evidence and no ticket text may reach the Meta Model API,
  ever. Every call site takes its client from
  `reader_client(tenant_uuid)`. When the local server is down,
  readers take the degraded path they already had: the capture is
  kept on the ticket, the technician reads it, and the request
  completes. The intake treats the ticket as it does with no key
  configured and releases it to the governor. When the tenant setting
  itself can't be read, no model is called at all, for the same
  reason.
- **No transcription for voice notes.** Glimmer takes no audio and
  Voice Transcribe runs only in the cloud. The audio stays on the
  server, the reading says the note is kept for the technician to
  listen to, and no transcription is made. The technician panel says
  the same in plain words.
- **No Muse Image guide photos.** A guide for a local tenant is drawn
  on this server with Pillow, as the fallback already did. The panel
  says so next to the guide form.
- **No SAM, no file upload, no search grounding on local.** The local
  client refuses those calls with a `LocalOnly` error instead of
  sending them anywhere.

## Measured times on the build machine

Hera (RTX 3050 8 GB, Ryzen 5 5600X, 62 GB RAM), Ollama 0.40.2,
`eyewitness-glimmer` (Q4_K_M, 27.9B, 12K context):

- Screen photo of the golden Windows 11 crash screen through
  `LocalClient` and the screen reader: **77.9 seconds** with the model
  loaded. It read the stop code `MEMORY_MANAGEMENT (0x1A)` and every
  line of the screen. The call log recorded kind `local`, 1,870 input
  tokens, 130 output tokens, $0.
- The synthetic Dell blink clip through the video reader: **50.1
  seconds** end to end, of which the keyframe description took 48.9
  seconds. The code locator found the light with no SAM call, code
  counted amber 2, white 3, and the Dell table gave the meaning
  (memory not detected). The keyframe description named the light
  turning white then amber.
- From cold, a first read took 110 seconds in Claude Code's earlier
  measurement on the same machine: 14 seconds to load, 33 seconds
  for 1,866 prompt tokens, 62 seconds for the answer. Plan for a
  minute or two per read.

## Setup

Environment variables, read by `app/eyewitness/meta/local.py`:

| Variable | Meaning | Default |
|---|---|---|
| `EYEWITNESS_LOCAL_BASE_URL` | The OpenAI-compatible endpoint, for example `http://127.0.0.1:11434/v1`. Required for local mode to be configured. | none |
| `EYEWITNESS_LOCAL_MODEL` | The model name the server answers to. | `eyewitness-glimmer` |
| `EYEWITNESS_LOCAL_API_KEY` | A bearer key where the server asks for one. A placeholder goes out when it is empty, which Ollama accepts. | empty |
| `EYEWITNESS_LOCAL_TIMEOUT_S` | Seconds to wait for one read. Local reads take a minute or two, so keep this well above the Meta timeouts. | `600` |
| `EYEWITNESS_LOCAL_EXTRA_BODY` | JSON merged into each request. The default switches reasoning off for Ollama. llama.cpp and vLLM name that switch differently, so the operator changes it here. | `{"reasoning_effort": "none"}` |

### Ollama

```bash
ollama pull muse-glimmer
ollama create eyewitness-glimmer -f eyewitness/docs/glimmer.Modelfile
ollama serve
```

The Modelfile in `eyewitness/docs/glimmer.Modelfile` sets a 12K
context and a temperature of 0.2 on the shared `muse-glimmer`
weights. Then:

```bash
EYEWITNESS_LOCAL_BASE_URL=http://127.0.0.1:11434/v1
```

### llama.cpp

Use build b10353 or newer. Glimmer needs its vision projector:

```bash
llama-server -m muse-glimmer-q4_k_m.gguf --mmproj mmproj-glimmer.gguf \
  --host 127.0.0.1 --port 8080 -c 12288
EYEWITNESS_LOCAL_BASE_URL=http://127.0.0.1:8080/v1
EYEWITNESS_LOCAL_MODEL=muse-glimmer
```

llama.cpp names the reasoning switch through its chat template
controls. Set `EYEWITNESS_LOCAL_EXTRA_BODY` to the field build
b10353 documents for the downloaded template, or leave reasoning on
and raise `EYEWITNESS_LOCAL_TIMEOUT_S`, because reasoning tokens add
a minute or more on a small card.

### vLLM

```bash
vllm serve <the Glimmer weights folder> --port 8000 \
  --max-model-len 12288
EYEWITNESS_LOCAL_BASE_URL=http://127.0.0.1:8000/v1
EYEWITNESS_LOCAL_MODEL=<the served model name>
EYEWITNESS_LOCAL_API_KEY=<the key the vLLM server was started with, if any>
```

vLLM takes its reasoning control through `chat_template_kwargs`.
Set `EYEWITNESS_LOCAL_EXTRA_BODY` to suit the template in use.

## Turning a tenant over

1. Set the environment above on the app and Celery services, then
   restart them.
2. Open Ticket settings. The choice "Your own hardware (Muse
   Glimmer)" is open now. Pick it and save.
3. New reads for that tenant go to the local server from that point.
   Requests already open keep the client their captures arrive
   under, because each capture read looks the tenant up again.

The daily Meta cap and the Meta key keep their old meaning for
tenants on `meta`. Local reads never touch either.

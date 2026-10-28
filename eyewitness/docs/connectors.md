# Connectors

All desk access goes through `app/eyewitness/connectors/base.py`:
`post_public_note`, `post_internal_note`, `attach_file`,
`customer_language`. New tickets arrive through Wegweiser's existing
inbound poll, and since Phase 5 through the Zammad push below. The
poll stays as the fallback for every desk.

- **Zammad** - implemented on the existing `ZammadClient`
  (`POST /ticket_articles`, `internal` true/false). Push setup and
  the signed endpoint sit in `app/eyewitness/webhook.py` and
  `app/eyewitness/api_routes.py`. One command makes the webhook and
  its ticket created trigger. See the API notes for the command.
- **Zendesk, Freshdesk, HaloPSA and ConnectWise** - documented stubs
  raising `NotImplementedError`, each carrying its API notes in the
  module docstring. A desk on this list connects today through the
  Evidence API: its ticket created event calls POST /requests, it
  posts the returned `customer_text` to the ticket, and a key
  callback address reports readings back. The per desk notes sit in
  the API document.

Any agent connects through the MCP server in `eyewitness/mcp/`,
which calls the same Evidence API. Its readings return inside the
untrusted fence, so the agent reads them as data.

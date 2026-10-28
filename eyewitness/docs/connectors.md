# Connectors

All desk access goes through `app/eyewitness/connectors/base.py`:
`post_public_note`, `post_internal_note`, `attach_file`,
`customer_language`. New tickets arrive through Wegweiser's existing
inbound poll, not through connectors.

- **Zammad** — implemented on the existing `ZammadClient`
  (`POST /ticket_articles`, `internal` true/false).
- **Zendesk, Freshdesk, HaloPSA, ConnectWise** — documented stubs
  raising `NotImplementedError`, each carrying its API notes in the
  module docstring. Roadmap (brief section 4, Stretch).

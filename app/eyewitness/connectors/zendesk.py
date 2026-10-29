"""Zendesk connector stub (roadmap). API notes: Tickets API v2,
POST /api/v2/tickets/{id}/comments with public=true/false for
visibility; uploads via POST /api/v2/uploads.json then referenced
from the comment. Implement HelpdeskConnector to enable."""
from .base import HelpdeskConnector


class ZendeskConnector(HelpdeskConnector):
    provider = "zendesk"

    def post_public_note(self, external_ticket_id, text, html=None):
        raise NotImplementedError("Zendesk connector is a documented stub")

    def post_internal_note(self, external_ticket_id, text, html=None):
        raise NotImplementedError("Zendesk connector is a documented stub")

    def attach_file(self, external_ticket_id, filename, data, mime):
        raise NotImplementedError("Zendesk connector is a documented stub")

    def customer_language(self, external_ticket_id):
        raise NotImplementedError("Zendesk connector is a documented stub")

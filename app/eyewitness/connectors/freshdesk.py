"""Freshdesk connector stub (roadmap). API notes: API v2,
POST /api/v2/tickets/{id}/reply (customer-visible) and
POST /api/v2/tickets/{id}/notes (private); attachments multipart
on both. Implement HelpdeskConnector to enable."""
from .base import HelpdeskConnector


class FreshdeskConnector(HelpdeskConnector):
    provider = "freshdesk"

    def post_public_note(self, external_ticket_id, text, html=None):
        raise NotImplementedError("Freshdesk connector is a documented stub")

    def post_internal_note(self, external_ticket_id, text, html=None):
        raise NotImplementedError("Freshdesk connector is a documented stub")

    def attach_file(self, external_ticket_id, filename, data, mime):
        raise NotImplementedError("Freshdesk connector is a documented stub")

    def customer_language(self, external_ticket_id):
        raise NotImplementedError("Freshdesk connector is a documented stub")

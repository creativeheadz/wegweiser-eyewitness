"""HaloPSA connector stub (roadmap). API notes: POST /api/Tickets
and ticket actions via /api/Tickets/{id} with action visibility
flags; OAuth2 client credentials. Implement HelpdeskConnector."""
from .base import HelpdeskConnector


class HaloPSAConnector(HelpdeskConnector):
    provider = "halopsa"

    def post_public_note(self, external_ticket_id, text):
        raise NotImplementedError("HaloPSA connector is a documented stub")

    def post_internal_note(self, external_ticket_id, text):
        raise NotImplementedError("HaloPSA connector is a documented stub")

    def attach_file(self, external_ticket_id, filename, data, mime):
        raise NotImplementedError("HaloPSA connector is a documented stub")

    def customer_language(self, external_ticket_id):
        raise NotImplementedError("HaloPSA connector is a documented stub")

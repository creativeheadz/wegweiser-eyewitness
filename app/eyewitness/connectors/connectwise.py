"""ConnectWise Manage connector stub (roadmap). API notes:
service/tickets/{id}/notes with internalFlag for visibility;
documents via /system/documents. Implement HelpdeskConnector."""
from .base import HelpdeskConnector


class ConnectWiseConnector(HelpdeskConnector):
    provider = "connectwise"

    def post_public_note(self, external_ticket_id, text, html=None):
        raise NotImplementedError("ConnectWise connector is a documented stub")

    def post_internal_note(self, external_ticket_id, text, html=None):
        raise NotImplementedError("ConnectWise connector is a documented stub")

    def attach_file(self, external_ticket_id, filename, data, mime):
        raise NotImplementedError("ConnectWise connector is a documented stub")

    def customer_language(self, external_ticket_id):
        raise NotImplementedError("ConnectWise connector is a documented stub")

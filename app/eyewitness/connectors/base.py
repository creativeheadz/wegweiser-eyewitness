"""Helpdesk connector interface (M3). New tickets arrive through
Wegweiser's existing inbound poll; a connector only writes notes,
attaches files, and reports the customer's language."""
from __future__ import annotations

from abc import ABC, abstractmethod


class HelpdeskConnector(ABC):
    provider = "base"

    @abstractmethod
    def post_public_note(self, external_ticket_id: str, text: str,
                         html: str | None = None) -> dict:
        """Customer-visible article (internal=False). Returns {article_id}.
        `html`, when given, is the same note set out for desks that
        render HTML; desks that do not keep the text."""

    @abstractmethod
    def post_internal_note(self, external_ticket_id: str, text: str,
                           html: str | None = None) -> dict:
        """Technician-only article (internal=True). Returns {article_id}."""

    @abstractmethod
    def attach_file(self, external_ticket_id: str, filename: str,
                    data: bytes, mime: str) -> dict:
        """Attach one capture file to the ticket."""

    @abstractmethod
    def customer_language(self, external_ticket_id: str) -> str:
        """ISO language of the customer, or 'en' when unknown."""

"""Zammad connector on the existing ZammadClient (M3).

Visibility is the desk's own switch: a customer-visible article is
`internal: false`, an internal note `internal: true` - the same shape
the case ticket code already posts with.
"""
from __future__ import annotations

import html

from .base import HelpdeskConnector


class ZammadConnector(HelpdeskConnector):
    provider = "zammad"

    def __init__(self, client):
        self._client = client  # app.utilities.zammad_client.ZammadClient

    @classmethod
    def for_tenant(cls, tenant_uuid):
        """(connector, None) or (None, reason) via the stored connection."""
        from app.utilities.zammad_sync import connected_client
        client, ctx = connected_client(tenant_uuid)
        if client is None:
            return None, ctx
        return cls(client), None

    @staticmethod
    def _html(text: str) -> str:
        return "<br>\n".join(html.escape(line) for line in (text or "").splitlines())

    def post_public_note(self, external_ticket_id: str, text: str,
                         html: str | None = None) -> dict:
        out = self._client.append_article(
            external_ticket_id, subject="More information needed",
            body_html=html or self._html(text), internal=False)
        return {"article_id": out.get("id") if isinstance(out, dict) else None}

    def post_internal_note(self, external_ticket_id: str, text: str,
                           html: str | None = None) -> dict:
        out = self._client.append_article(
            external_ticket_id, subject="Eyewitness evidence",
            body_html=html or self._html(text), internal=True)
        return {"article_id": out.get("id") if isinstance(out, dict) else None}

    def attach_file(self, external_ticket_id: str, filename: str,
                    data: bytes, mime: str) -> dict:
        out = self._client.append_article(
            external_ticket_id, subject=filename,
            body_html=self._html(f"Attached: {filename}"), internal=True)
        # ZammadClient.append_article has no attachment parameter today;
        # Phase 2 extends delivery to carry the file bytes. The article
        # above keeps the ticket truthful until then.
        return {"article_id": out.get("id") if isinstance(out, dict) else None,
                "attached": False}

    def customer_language(self, external_ticket_id: str) -> str:
        return "en"

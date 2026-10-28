"""Eyewitness MCP server over the Evidence API.

Run with stdio for a local agent, or with streamable HTTP for a
shared one:

    EYEWITNESS_API_URL=https://staging.example EYEWITNESS_API_KEY=ew_... \
        python -m eyewitness.mcp.server --transport stdio

Readings return inside the customer_evidence fence. The calling
agent sees observations from a person, with flags in view.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from eyewitness.mcp.client import EvidenceClient  # noqa: E402

TOOL_SCHEMAS = [
    {"name": "list_templates",
     "description": "List the evidence templates and quick questions "
                    "the Evidence API can ask for.",
     "inputSchema": {"type": "object", "properties": {},
                     "additionalProperties": False}},
    {"name": "request_evidence",
     "description": "Ask a customer for evidence. Give template names, "
                    "or set auto with the ticket text and let the "
                    "intake agent choose. The reply carries the "
                    "customer text, the phone link and its expiry.",
     "inputSchema": {
         "type": "object",
         "properties": {
             "templates": {"type": "array",
                           "items": {"type": "string"}},
             "questions": {"type": "array",
                           "items": {"type": "string"}},
             "auto": {"type": "boolean"},
             "ticket_text": {"type": "string"},
             "external_ref": {"type": "string"}},
         "additionalProperties": False}},
    {"name": "get_evidence",
     "description": "Read one request: status, items and readings. "
                    "Readings sit inside the customer_evidence "
                    "untrusted fence and carry injection flags.",
     "inputSchema": {
         "type": "object",
         "properties": {"request_id": {"type": "integer"}},
         "required": ["request_id"], "additionalProperties": False}},
    {"name": "release_evidence",
     "description": "Release one request so its ticket moves on.",
     "inputSchema": {
         "type": "object",
         "properties": {"request_id": {"type": "integer"}},
         "required": ["request_id"], "additionalProperties": False}},
    {"name": "cancel_evidence",
     "description": "Cancel one request. Its link stops working.",
     "inputSchema": {
         "type": "object",
         "properties": {"request_id": {"type": "integer"}},
         "required": ["request_id"], "additionalProperties": False}},
]


def get_client() -> EvidenceClient:
    return EvidenceClient()


def list_templates() -> dict:
    return get_client().list_templates()


def request_evidence(templates: list | None = None,
                     questions: list | None = None, auto: bool = False,
                     ticket_text: str = "", external_ref: str = "") -> dict:
    return get_client().request_evidence(
        templates=templates, questions=questions, auto=auto,
        ticket_text=ticket_text, external_ref=external_ref)


def get_evidence(request_id: int) -> dict:
    return get_client().get_evidence(request_id)


def release_evidence(request_id: int) -> dict:
    return get_client().release_evidence(request_id)


def cancel_evidence(request_id: int) -> dict:
    return get_client().cancel_evidence(request_id)


def build_server(host: str = "127.0.0.1", port: int = 8000):
    """FastMCP server with the five tools registered."""
    from mcp.server.fastmcp import FastMCP
    server = FastMCP("eyewitness", host=host, port=port)
    server.tool()(list_templates)
    server.tool()(request_evidence)
    server.tool()(get_evidence)
    server.tool()(release_evidence)
    server.tool()(cancel_evidence)
    return server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", default="stdio",
                        choices=("stdio", "streamable-http"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    server = build_server(host=args.host, port=args.port)
    if args.transport == "streamable-http":
        server.run(transport="streamable-http")
    else:
        server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

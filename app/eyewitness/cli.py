"""Eyewitness command line: webhook setup and API keys (Phase 5).

The Zammad admin token and every new secret arrive at run time and
are never stored beyond the secret store entries they belong in.
Plaintext API keys print once, at creation, and nowhere else.
"""
from __future__ import annotations

import os

import click
from flask.cli import with_appcontext


def create_api_key_row(tenant_uuid, *, name: str, scopes: list,
                       callback_url: str | None):
    """Create one key row. Returns (row, plaintext, callback_secret)."""
    import secrets as secrets_mod
    import uuid as uuid_mod

    from app.models import db
    from app.eyewitness import api as ew_api
    from app.eyewitness.models import ApiKey

    tenant = (tenant_uuid if isinstance(tenant_uuid, uuid_mod.UUID)
              else uuid_mod.UUID(str(tenant_uuid)))
    plaintext, prefix, hashed = ew_api.generate_api_key()
    row = ApiKey(tenant_uuid=tenant, name=name[:120], key_prefix=prefix,
                 key_hash=hashed, scopes=list(scopes),
                 callback_url=callback_url or None)
    db.session.add(row)
    db.session.flush()
    callback_secret = None
    if callback_url:
        callback_secret = secrets_mod.token_urlsafe(32)
        from app.utilities.secret_manager import set_secret
        from app.eyewitness.webhook import callback_secret_name
        set_secret(callback_secret_name(row.id), callback_secret)
    db.session.commit()
    return row, plaintext, callback_secret


@click.command("setup-zammad-webhook")
@click.option("--tenant", required=True, help="Tenant UUID.")
@click.option("--admin-token", default=None,
              help="Zammad admin token for this run only. Never stored. "
                   "Defaults to ZAMMAD_ADMIN_TOKEN.")
@click.option("--base-url", default="",
              help="Public app base URL for the hook address.")
@click.option("--endpoint", default="",
              help="Zammad API base. Defaults to the stored endpoint.")
@with_appcontext
def setup_zammad_webhook_cmd(tenant, admin_token, base_url, endpoint):
    """Create the Zammad webhook and ticket created trigger."""
    token = admin_token or os.environ.get("ZAMMAD_ADMIN_TOKEN") or ""
    if not token:
        token = click.prompt("Zammad admin token", hide_input=True)
    import uuid as uuid_mod
    from app.eyewitness.webhook import setup_zammad_webhook
    out = setup_zammad_webhook(uuid_mod.UUID(tenant), admin_token=token,
                               base_url=base_url, endpoint=endpoint)
    click.echo(f"Hook URL: {out['hook_url']}")
    click.echo(f"Zammad webhook id: {out['webhook_id']}")
    click.echo(f"Zammad trigger id: {out['trigger_id']}")


@click.command("create-api-key")
@click.option("--tenant", required=True, help="Tenant UUID.")
@click.option("--name", default="Evidence API", help="Key label.")
@click.option("--scopes", default="evidence:write,evidence:read",
              help="Comma list from evidence:write, evidence:read.")
@click.option("--callback-url", default="",
              help="Optional signed callback address.")
@with_appcontext
def create_api_key_cmd(tenant, name, scopes, callback_url):
    """Create one Evidence API key. The key prints once."""
    from app.eyewitness import api as ew_api
    from app.utilities.ssrf_guard import SSRFError, assert_safe_url
    wanted = [s.strip() for s in scopes.split(",") if s.strip()]
    bad = [s for s in wanted if s not in ew_api.SCOPES]
    if bad:
        raise click.ClickException(f"unknown scopes: {', '.join(bad)}")
    if callback_url:
        try:
            assert_safe_url(callback_url, block_private=True)
        except SSRFError as exc:
            raise click.ClickException(f"callback address refused: {exc}")
    row, plaintext, callback_secret = create_api_key_row(
        tenant, name=name, scopes=wanted,
        callback_url=callback_url or None)
    click.echo(f"Key id: {row.id}")
    click.echo(f"Prefix: {row.key_prefix}")
    click.echo(f"API key (shown once): {plaintext}")
    if callback_secret:
        click.echo(f"Callback secret (shown once): {callback_secret}")


@click.command("revoke-api-key")
@click.option("--tenant", required=True, help="Tenant UUID.")
@click.option("--key-id", required=True, type=int, help="Key id.")
@with_appcontext
def revoke_api_key_cmd(tenant, key_id):
    """Revoke one Evidence API key for one tenant."""
    import time
    from app.models import db
    from app.eyewitness.models import ApiKey
    row = db.session.get(ApiKey, key_id)
    if row is None or str(row.tenant_uuid) != str(tenant):
        raise click.ClickException("key not found for this tenant")
    row.revoked_at = int(time.time())
    db.session.commit()
    click.echo(f"Revoked key {row.id}.")


@click.command("catalogue-check")
@click.argument("path", required=False, default=None)
@click.option("--show", is_flag=True,
              help="Also print the vision prompt and schema per template.")
def catalogue_check_cmd(path, show):
    """Validate catalogue files. Exit code 1 on any error."""
    import json as _json
    from app.eyewitness import catalogue as _cat
    if path:
        # A path outside the shipped folder is an operator file or
        # folder, so it meets the operator rules it will meet at load.
        entries = [(path, not _cat.is_built_in_path(path))]
    else:
        entries = [(_cat._built_in_dir(), False)]
        op = os.environ.get("EYEWITNESS_CATALOGUE_DIR") or ""
        if op:
            entries.append((op, True))
    rows = _cat.check_paths(entries)
    bad = False
    for name, spec, error in rows:
        if error or spec is None:
            click.echo(f"{name}: {error or 'invalid'}")
            bad = True
            continue
        click.echo(f"{spec['id']} {spec['kind']} {spec['reader']} OK")
        if show and spec.get("reader") == "vision":
            from app.eyewitness.readers.vision import build_schema, build_system
            click.echo(build_system(spec))
            click.echo(_json.dumps(build_schema(spec), indent=2, sort_keys=True))
    if bad:
        raise SystemExit(1)


@click.command("show-hook")
@click.option("--tenant", required=True, help="Tenant UUID.")
@click.option("--base-url", default="", help="Public app base URL.")
@with_appcontext
def show_hook_cmd(tenant, base_url):
    """Print the Zammad hook address for one tenant."""
    import uuid as uuid_mod
    from app.eyewitness.webhook import ensure_hook, hook_url_for
    tenant_uuid = uuid_mod.UUID(tenant)
    ensure_hook(tenant_uuid)
    click.echo(hook_url_for(tenant_uuid, base_url))

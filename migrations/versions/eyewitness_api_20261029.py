"""Eyewitness Phase 5 connectivity: API keys, webhook identity, callbacks.

Revision ID: eyewitness_api_20261029
Revises: eyewitness_retention_20261028
Create Date: 2026-10-29

Additive and idempotent, in the house style: every change checks
first, so it can be pre-applied before the push that needs it. The
webhook HMAC secret and every callback secret stay in the secret
store and never enter this migration.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'eyewitness_api_20261029'
down_revision = 'eyewitness_retention_20261028'
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()


def _cols(insp, table):
    if not insp.has_table(table):
        return set()
    return {c['name'] for c in insp.get_columns(table)}


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if 'eyewitness_api_keys' not in tables:
        op.create_table(
            'eyewitness_api_keys',
            sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column('tenant_uuid', UUID, sa.ForeignKey('tenants.tenantuuid', ondelete='CASCADE'), nullable=False),
            sa.Column('name', sa.String(120), nullable=False, server_default=''),
            sa.Column('key_prefix', sa.String(16), nullable=False),
            sa.Column('key_hash', sa.String(64), nullable=False),
            sa.Column('scopes', JSONB, nullable=False, server_default='[]'),
            sa.Column('callback_url', sa.String(500), nullable=True),
            sa.Column('last_used_at', sa.BigInteger(), nullable=True),
            sa.Column('revoked_at', sa.BigInteger(), nullable=True),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
            sa.Column('created_by', UUID, nullable=True),
        )
        op.create_index('ix_eyewitness_api_keys_tenant_uuid', 'eyewitness_api_keys', ['tenant_uuid'])
        op.create_index('ix_eyewitness_api_keys_key_prefix', 'eyewitness_api_keys', ['key_prefix'])

    if 'eyewitness_callback_deliveries' not in tables:
        op.create_table(
            'eyewitness_callback_deliveries',
            sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column('api_key_id', sa.BigInteger(), sa.ForeignKey('eyewitness_api_keys.id', ondelete='CASCADE'), nullable=False),
            sa.Column('request_id', sa.BigInteger(), sa.ForeignKey('eyewitness_requests.id', ondelete='CASCADE'), nullable=False),
            sa.Column('event', sa.String(32), nullable=False),
            sa.Column('payload', JSONB, nullable=True),
            sa.Column('status', sa.String(16), nullable=False, server_default='pending'),
            sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('last_error', sa.Text(), nullable=True),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
            sa.Column('delivered_at', sa.BigInteger(), nullable=True),
            sa.Column('next_attempt_at', sa.BigInteger(), nullable=True),
        )
        op.create_index('ix_eyewitness_callback_deliveries_api_key_id', 'eyewitness_callback_deliveries', ['api_key_id'])
        op.create_index('ix_eyewitness_callback_deliveries_request_id', 'eyewitness_callback_deliveries', ['request_id'])

    cols = _cols(insp, 'eyewitness_tenant_settings')
    if cols and 'webhook_hook_id' not in cols:
        op.add_column('eyewitness_tenant_settings', sa.Column('webhook_hook_id', sa.String(64), nullable=True))
        op.create_index('ix_eyewitness_tenant_settings_webhook_hook_id', 'eyewitness_tenant_settings', ['webhook_hook_id'], unique=True)

    cols = _cols(insp, 'eyewitness_requests')
    if cols and 'source' not in cols:
        op.add_column('eyewitness_requests', sa.Column('source', sa.String(16), nullable=True, server_default='desk'))
    if cols and 'external_ref' not in cols:
        op.add_column('eyewitness_requests', sa.Column('external_ref', sa.String(200), nullable=True))
    if cols and 'api_key_id' not in cols:
        op.add_column('eyewitness_requests', sa.Column('api_key_id', sa.BigInteger(), nullable=True))
        op.create_foreign_key('fk_eyewitness_requests_api_key_id', 'eyewitness_requests', 'eyewitness_api_keys', ['api_key_id'], ['id'], ondelete='SET NULL')
        op.create_index('ix_eyewitness_requests_api_key_id', 'eyewitness_requests', ['api_key_id'])
    if cols:
        try:
            op.alter_column('eyewitness_requests', 'helpdesk_ticket_id', existing_type=sa.BigInteger(), nullable=True)
        except Exception:
            pass


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = _cols(insp, 'eyewitness_requests')
    if 'api_key_id' in cols:
        try:
            op.drop_constraint('fk_eyewitness_requests_api_key_id', 'eyewitness_requests', type_='foreignkey')
        except Exception:
            pass
        try:
            op.drop_index('ix_eyewitness_requests_api_key_id', table_name='eyewitness_requests')
        except Exception:
            pass
        op.drop_column('eyewitness_requests', 'api_key_id')
    if 'external_ref' in cols:
        op.drop_column('eyewitness_requests', 'external_ref')
    if 'source' in cols:
        op.drop_column('eyewitness_requests', 'source')
    cols = _cols(insp, 'eyewitness_tenant_settings')
    if 'webhook_hook_id' in cols:
        try:
            op.drop_index('ix_eyewitness_tenant_settings_webhook_hook_id', table_name='eyewitness_tenant_settings')
        except Exception:
            pass
        op.drop_column('eyewitness_tenant_settings', 'webhook_hook_id')
    if insp.has_table('eyewitness_callback_deliveries'):
        op.drop_table('eyewitness_callback_deliveries')
    if insp.has_table('eyewitness_api_keys'):
        op.drop_table('eyewitness_api_keys')

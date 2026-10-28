"""Eyewitness tables: intake runs, evidence requests, captures, Meta
call log + cache, per-tenant switches (brief 7.1).

Revision ID: eyewitness_20261027
Revises: exe_audit_indexes_20261024
Create Date: 2026-10-27

Additive and idempotent, in the house style: every create checks
first, so it can be pre-applied before the push that needs it.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'eyewitness_20261027'
down_revision = 'exe_audit_indexes_20261024'
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if 'eyewitness_intake_runs' not in tables:
        op.create_table(
            'eyewitness_intake_runs',
            sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column('tenant_uuid', UUID, sa.ForeignKey('tenants.tenantuuid', ondelete='CASCADE'), nullable=False),
            sa.Column('helpdesk_ticket_id', sa.BigInteger(), sa.ForeignKey('helpdesk_tickets.id', ondelete='CASCADE'), nullable=False),
            sa.Column('deviceuuid', UUID, sa.ForeignKey('devices.deviceuuid', ondelete='SET NULL'), nullable=True),
            sa.Column('origin', sa.String(16), nullable=False),
            sa.Column('status', sa.String(24), nullable=False, server_default='assessing'),
            sa.Column('assessment', JSONB, nullable=True),
            sa.Column('model', sa.String(80), nullable=True),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
            sa.Column('released_at', sa.BigInteger(), nullable=True),
            sa.Column('error', sa.Text(), nullable=True),
        )
        op.create_index('ix_eyewitness_intake_runs_tenant_uuid', 'eyewitness_intake_runs', ['tenant_uuid'])
        op.create_index('ix_eyewitness_intake_runs_helpdesk_ticket_id', 'eyewitness_intake_runs', ['helpdesk_ticket_id'])

    if 'eyewitness_requests' not in tables:
        op.create_table(
            'eyewitness_requests',
            sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column('tenant_uuid', UUID, sa.ForeignKey('tenants.tenantuuid', ondelete='CASCADE'), nullable=False),
            sa.Column('intake_run_id', sa.BigInteger(), sa.ForeignKey('eyewitness_intake_runs.id', ondelete='SET NULL'), nullable=True),
            sa.Column('helpdesk_ticket_id', sa.BigInteger(), sa.ForeignKey('helpdesk_tickets.id', ondelete='CASCADE'), nullable=False),
            sa.Column('token', sa.String(64), nullable=False, unique=True),
            sa.Column('items', JSONB, nullable=False),
            sa.Column('customer_text', sa.Text(), nullable=True),
            sa.Column('reason_for_technician', sa.Text(), nullable=True),
            sa.Column('language', sa.String(8), nullable=True),
            sa.Column('status', sa.String(20), nullable=False, server_default='open'),
            sa.Column('approved_by', UUID, nullable=True),
            sa.Column('approved_at', sa.BigInteger(), nullable=True),
            sa.Column('filter_hits', JSONB, nullable=True),
            sa.Column('delivered_via', JSONB, nullable=True),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
            sa.Column('expires_at', sa.BigInteger(), nullable=False),
            sa.Column('opened_at', sa.BigInteger(), nullable=True),
            sa.Column('submitted_at', sa.BigInteger(), nullable=True),
            sa.Column('voice_offer', sa.String(16), nullable=False, server_default='not_shown'),
        )
        op.create_index('ix_eyewitness_requests_tenant_uuid', 'eyewitness_requests', ['tenant_uuid'])
        op.create_index('ix_eyewitness_requests_helpdesk_ticket_id', 'eyewitness_requests', ['helpdesk_ticket_id'])
        op.create_index('ix_eyewitness_requests_token', 'eyewitness_requests', ['token'])

    if 'eyewitness_captures' not in tables:
        op.create_table(
            'eyewitness_captures',
            sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column('request_id', sa.BigInteger(), sa.ForeignKey('eyewitness_requests.id', ondelete='CASCADE'), nullable=False),
            sa.Column('item_id', sa.String(64), nullable=False),
            sa.Column('kind', sa.String(24), nullable=False),
            sa.Column('file_path', sa.String(500), nullable=True),
            sa.Column('mime', sa.String(80), nullable=True),
            sa.Column('size', sa.BigInteger(), nullable=True),
            sa.Column('sha256', sa.String(64), nullable=True),
            sa.Column('status', sa.String(16), nullable=False, server_default='uploaded'),
            sa.Column('skip_reason', sa.String(200), nullable=True),
            sa.Column('quality', JSONB, nullable=True),
            sa.Column('reading', JSONB, nullable=True),
            sa.Column('retake', JSONB, nullable=True),
            sa.Column('injection_flag', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('prompt_version', sa.String(32), nullable=True),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
            sa.Column('processed_at', sa.BigInteger(), nullable=True),
            sa.Column('error', sa.Text(), nullable=True),
        )
        op.create_index('ix_eyewitness_captures_request_id', 'eyewitness_captures', ['request_id'])

    if 'eyewitness_meta_calls' not in tables:
        op.create_table(
            'eyewitness_meta_calls',
            sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column('intake_run_id', sa.BigInteger(), sa.ForeignKey('eyewitness_intake_runs.id', ondelete='SET NULL'), nullable=True),
            sa.Column('capture_id', sa.BigInteger(), sa.ForeignKey('eyewitness_captures.id', ondelete='SET NULL'), nullable=True),
            sa.Column('kind', sa.String(16), nullable=False),
            sa.Column('model', sa.String(80), nullable=True),
            sa.Column('input_tokens', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('cached_tokens', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('output_tokens', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('units', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('usd', sa.Float(), nullable=False, server_default='0'),
            sa.Column('cache_hit', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('ms', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
        )

    if 'eyewitness_meta_cache' not in tables:
        op.create_table(
            'eyewitness_meta_cache',
            sa.Column('key', sa.String(64), primary_key=True),
            sa.Column('response', sa.Text(), nullable=False),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
        )

    if 'eyewitness_tenant_settings' not in tables:
        op.create_table(
            'eyewitness_tenant_settings',
            sa.Column('tenant_uuid', UUID, sa.ForeignKey('tenants.tenantuuid', ondelete='CASCADE'), primary_key=True),
            sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('auto_send_templates', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('updated_at', sa.BigInteger(), nullable=False),
        )


def downgrade():
    op.drop_table('eyewitness_tenant_settings')
    op.drop_table('eyewitness_meta_cache')
    op.drop_table('eyewitness_meta_calls')
    op.drop_table('eyewitness_captures')
    op.drop_table('eyewitness_requests')
    op.drop_table('eyewitness_intake_runs')

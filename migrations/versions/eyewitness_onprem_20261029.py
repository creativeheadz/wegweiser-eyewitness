"""Eyewitness Phase 6: readers on the tenant's own hardware.

Revision ID: eyewitness_onprem_20261029
Revises: eyewitness_api_20261029
Create Date: 2026-10-29

Additive only. ``reader_backend`` on ``eyewitness_tenant_settings``
holds ``meta`` (the Meta Model API) or ``local`` (Muse Glimmer on
the tenant's own hardware). The server default keeps every existing
tenant on ``meta``.
"""
from alembic import op
import sqlalchemy as sa

revision = 'eyewitness_onprem_20261029'
down_revision = 'eyewitness_api_20261029'
branch_labels = None
depends_on = None


def _cols(insp, table):
    if not insp.has_table(table):
        return set()
    return {c['name'] for c in insp.get_columns(table)}


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = _cols(insp, 'eyewitness_tenant_settings')
    if cols and 'reader_backend' not in cols:
        op.add_column(
            'eyewitness_tenant_settings',
            sa.Column('reader_backend', sa.String(16), nullable=False,
                      server_default='meta'))


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = _cols(insp, 'eyewitness_tenant_settings')
    if 'reader_backend' in cols:
        op.drop_column('eyewitness_tenant_settings', 'reader_backend')

"""Eyewitness retention marks: purged_at on requests and captures.

Revision ID: eyewitness_retention_20261028
Revises: eyewitness_20261027
Create Date: 2026-10-28

Additive and idempotent, in the house style: every change checks
first, so it can be pre-applied before the push that needs it. The
sweep deletes capture files after EYEWITNESS_RETENTION_DAYS and
stamps these columns; a null stamp means the file is still kept.
"""
from alembic import op
import sqlalchemy as sa

revision = 'eyewitness_retention_20261028'
down_revision = 'eyewitness_20261027'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table in ('eyewitness_requests', 'eyewitness_captures'):
        cols = {c['name'] for c in insp.get_columns(table)} \
            if insp.has_table(table) else set()
        if cols and 'purged_at' not in cols:
            op.add_column(table, sa.Column('purged_at',
                                           sa.BigInteger(),
                                           nullable=True))


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table in ('eyewitness_captures', 'eyewitness_requests'):
        cols = {c['name'] for c in insp.get_columns(table)} \
            if insp.has_table(table) else set()
        if 'purged_at' in cols:
            op.drop_column(table, 'purged_at')

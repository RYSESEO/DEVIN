"""add bot_runs table

Revision ID: f3a2b7c91d44
Revises: e82d36945e19
Create Date: 2026-06-03 14:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f3a2b7c91d44'
down_revision: Union[str, Sequence[str], None] = 'e82d36945e19'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add bot_runs table for monitoring bot run tracking."""
    op.create_table('bot_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_type', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('services_checked', sa.Integer(), nullable=True),
        sa.Column('urls_checked', sa.Integer(), nullable=True),
        sa.Column('changes_detected', sa.Integer(), nullable=True),
        sa.Column('errors', sa.Integer(), nullable=True),
        sa.Column('stale_flags_created', sa.Integer(), nullable=True),
        sa.Column('webhooks_fired', sa.Integer(), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('error_detail', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_bot_runs_id'), 'bot_runs', ['id'], unique=False)


def downgrade() -> None:
    """Remove bot_runs table."""
    op.drop_index(op.f('ix_bot_runs_id'), table_name='bot_runs')
    op.drop_table('bot_runs')

"""add stripe columns to api_keys

Revision ID: a7c4e2f10b58
Revises: f3a2b7c91d44
Create Date: 2026-06-04 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7c4e2f10b58'
down_revision: Union[str, Sequence[str], None] = 'f3a2b7c91d44'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Link API keys to their Stripe customer/subscription for self-serve billing."""
    op.add_column('api_keys', sa.Column('stripe_customer_id', sa.String(), nullable=True))
    op.add_column('api_keys', sa.Column('stripe_subscription_id', sa.String(), nullable=True))
    op.create_index(
        op.f('ix_api_keys_stripe_customer_id'), 'api_keys', ['stripe_customer_id'], unique=False
    )
    op.create_index(
        op.f('ix_api_keys_stripe_subscription_id'),
        'api_keys',
        ['stripe_subscription_id'],
        unique=False,
    )


def downgrade() -> None:
    """Remove Stripe linkage columns."""
    op.drop_index(op.f('ix_api_keys_stripe_subscription_id'), table_name='api_keys')
    op.drop_index(op.f('ix_api_keys_stripe_customer_id'), table_name='api_keys')
    op.drop_column('api_keys', 'stripe_subscription_id')
    op.drop_column('api_keys', 'stripe_customer_id')

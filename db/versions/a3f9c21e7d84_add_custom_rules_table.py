"""add custom_rules table

Revision ID: a3f9c21e7d84
Revises: b8151bef1165
Create Date: 2026-09-08 19:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f9c21e7d84'
down_revision: Union[str, Sequence[str], None] = 'b8151bef1165'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # A brand-new table with no foreign key to any existing one (rules
    # target column names/types abstractly, independent of any upload) —
    # unlike 5e88d2c39f94's audit_log.record_id addition, this needs no
    # SQLite batch-mode recreate.
    op.create_table(
        "custom_rules",
        sa.Column("rule_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("target_kind", sa.String(20), nullable=False),
        sa.Column("target_value", sa.String(255), nullable=False),
        sa.Column("condition_operator", sa.String(20), nullable=False),
        sa.Column("condition_value", sa.Text(), nullable=True),
        sa.Column("action", sa.String(20), nullable=False, server_default="flag"),
        sa.Column("severity", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_custom_rules_target", "custom_rules", ["target_kind", "target_value"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_custom_rules_target", table_name="custom_rules")
    op.drop_table("custom_rules")

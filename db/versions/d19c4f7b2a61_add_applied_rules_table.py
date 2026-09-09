"""add applied_rules table

Revision ID: d19c4f7b2a61
Revises: a3f9c21e7d84
Create Date: 2026-09-09 14:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd19c4f7b2a61'
down_revision: Union[str, Sequence[str], None] = 'a3f9c21e7d84'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "applied_rules",
        sa.Column("applied_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("upload_id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["upload_id"], ["raw_uploads.upload_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rule_id"], ["custom_rules.rule_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("upload_id", "rule_id", name="uq_applied_rules_upload_rule"),
    )
    op.create_index("ix_applied_rules_upload_id", "applied_rules", ["upload_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_applied_rules_upload_id", table_name="applied_rules")
    op.drop_table("applied_rules")

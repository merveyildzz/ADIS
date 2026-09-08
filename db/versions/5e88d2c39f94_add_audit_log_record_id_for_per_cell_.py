"""add audit_log.record_id for per-cell lineage

Revision ID: 5e88d2c39f94
Revises: 54d337cae21d
Create Date: 2026-09-08 17:48:11.111652

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5e88d2c39f94'
down_revision: Union[str, Sequence[str], None] = '54d337cae21d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # SQLite can't ALTER a table to add a FK constraint directly — batch
    # mode recreates the table under the hood (copy-and-move strategy).
    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.add_column(sa.Column("record_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_audit_log_record_id", ["record_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_audit_log_record_id_cleaned_records", "cleaned_records", ["record_id"], ["record_id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.drop_constraint("fk_audit_log_record_id_cleaned_records", type_="foreignkey")
        batch_op.drop_index("ix_audit_log_record_id")
        batch_op.drop_column("record_id")

"""scope custom_rules to an upload (add upload_id)

Revision ID: f27a8e91c3d5
Revises: d19c4f7b2a61
Create Date: 2026-09-10 09:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f27a8e91c3d5'
down_revision: Union[str, Sequence[str], None] = 'd19c4f7b2a61'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # SQLite can't ALTER a table to add a FK constraint directly — batch
    # mode recreates the table under the hood (same pattern as
    # 5e88d2c39f94's audit_log.record_id addition).
    with op.batch_alter_table("custom_rules", schema=None) as batch_op:
        batch_op.add_column(sa.Column("upload_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_custom_rules_upload_id", ["upload_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_custom_rules_upload_id_raw_uploads", "raw_uploads", ["upload_id"], ["upload_id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("custom_rules", schema=None) as batch_op:
        batch_op.drop_constraint("fk_custom_rules_upload_id_raw_uploads", type_="foreignkey")
        batch_op.drop_index("ix_custom_rules_upload_id")
        batch_op.drop_column("upload_id")

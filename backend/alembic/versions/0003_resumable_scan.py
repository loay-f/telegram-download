"""remember how far an unfinished scan got

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-18

A scan that is cut short (connection lost, app restarted) used to start again
from the chat's newest message. Its position is now saved with every batch, so
the next scan continues below it.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("chats") as batch:
        batch.add_column(sa.Column("scan_top_msg_id", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("scan_resume_msg_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("chats") as batch:
        batch.drop_column("scan_resume_msg_id")
        batch.drop_column("scan_top_msg_id")

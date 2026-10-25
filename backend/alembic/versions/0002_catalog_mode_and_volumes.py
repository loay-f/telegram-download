"""catalog-only tracking, per-chat auto-download, multi-volume grouping

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-25

Tracking a chat now only scans it: new files become `available` and are
downloaded when the user picks them (or when the chat has auto_download on).
Files that were queued but never started become `available` too.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("chats") as batch:
        batch.add_column(sa.Column("auto_download", sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table("documents") as batch:
        # '' = no volume detected; NULL = not computed yet (filled at startup).
        batch.add_column(sa.Column("series_key", sa.Text(), nullable=True))
        batch.add_column(sa.Column("volume", sa.Integer(), nullable=True))
        batch.create_index("ix_documents_series_key", ["series_key"])
    op.execute(
        "UPDATE documents SET status = 'available', priority = 0, next_attempt_at = NULL "
        "WHERE status IN ('pending', 'downloading')"
    )


def downgrade() -> None:
    op.execute("UPDATE documents SET status = 'pending' WHERE status = 'available'")
    with op.batch_alter_table("documents") as batch:
        batch.drop_index("ix_documents_series_key")
        batch.drop_column("volume")
        batch.drop_column("series_key")
    with op.batch_alter_table("chats") as batch:
        batch.drop_column("auto_download")

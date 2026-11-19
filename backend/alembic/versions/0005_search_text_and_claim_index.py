"""search text column, index for claiming downloads

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-18

Photos and videos make libraries of hundreds of thousands of files, and two
things did not keep up:

- The library's search folded every name and caption (a Python function
  called by SQLite) for every row on every search. The folded text is now
  stored in `documents.search_text`; rows that exist already are filled at
  startup.
- Taking the next file to download sorted the whole queue. The new index
  holds the queue in the order it is claimed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("search_text", sa.Text(), nullable=True))
    op.drop_index("ix_documents_status_priority", table_name="documents")
    op.create_index(
        "ix_documents_claim", "documents", ["status", sa.text("priority DESC"), "first_seen_at", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_documents_claim", table_name="documents")
    op.create_index("ix_documents_status_priority", "documents", ["status", "priority"])
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("search_text")

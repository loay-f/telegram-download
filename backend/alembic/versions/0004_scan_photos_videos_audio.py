"""scan photos, videos and audio too

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-18

Scans used to ask Telegram only for messages with a plain file. Photos and
videos, and audio, are now separate passes over a chat, each with its own
position; they start from the beginning, so chats already scanned get their
older media listed. Images, video and audio also join the file types saved in
the settings (the new default).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MEDIA_EXTS = (
    "jpg,jpeg,png,webp,gif,heic,heif,bmp,tif,tiff,"
    "mp4,m4v,mkv,mov,avi,webm,3gp,wmv,flv,mpg,mpeg,"
    "mp3,m4a,ogg,oga,opus,wav,flac,aac,wma,amr"
)


def upgrade() -> None:
    with op.batch_alter_table("chats") as batch:
        for kind in ("media", "audio"):
            batch.add_column(sa.Column(f"{kind}_scanned_msg_id", sa.BigInteger(), nullable=False, server_default="0"))
            batch.add_column(sa.Column(f"{kind}_top_msg_id", sa.BigInteger(), nullable=True))
            batch.add_column(sa.Column(f"{kind}_resume_msg_id", sa.BigInteger(), nullable=True))

    bind = op.get_bind()
    saved = bind.execute(sa.text("SELECT value FROM settings WHERE key = 'allowed_exts'")).scalar()
    if saved is not None:
        have = [e for e in saved.split(",") if e]
        merged = sorted({*have, *MEDIA_EXTS.split(",")})
        bind.execute(
            sa.text("UPDATE settings SET value = :v WHERE key = 'allowed_exts'"), {"v": ",".join(merged)}
        )


def downgrade() -> None:
    with op.batch_alter_table("chats") as batch:
        for kind in ("audio", "media"):
            batch.drop_column(f"{kind}_resume_msg_id")
            batch.drop_column(f"{kind}_top_msg_id")
            batch.drop_column(f"{kind}_scanned_msg_id")

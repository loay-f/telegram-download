"""Checks the real Telethon adapter against genuine Telethon objects (no network)."""

from datetime import datetime, timezone

import pytest
from telethon import errors
from telethon.tl import types

from tglib.engine.errors import ChatUnavailable, FileRefExpired, FloodWait, NotAuthorized, NotConnected
from tglib.engine.telegram import TelethonGateway


@pytest.fixture
def gw(tmp_path):
    return TelethonGateway(tmp_path / "test", 12345, "0123456789abcdef0123456789abcdef")


def make_message(attributes, mime="application/pdf", caption="وصف الكتاب"):
    doc = types.Document(
        id=987654321,
        access_hash=1,
        file_reference=b"ref",
        date=datetime(2024, 5, 1, tzinfo=timezone.utc),
        mime_type=mime,
        size=4096,
        dc_id=2,
        attributes=attributes,
    )
    return types.Message(
        id=42,
        peer_id=types.PeerChannel(channel_id=1234),
        date=datetime(2024, 5, 1, 12, 30, tzinfo=timezone.utc),
        message=caption,
        media=types.MessageMediaDocument(document=doc),
    )


def test_document_message_with_filename(gw):
    msg = make_message([types.DocumentAttributeFilename(file_name="تفسير الطبري.PDF")])
    dm = gw._to_doc_message(-1001234, msg)
    assert dm.chat_id == -1001234 and dm.msg_id == 42
    assert dm.date == datetime(2024, 5, 1, 12, 30)  # naive UTC
    assert dm.caption == "وصف الكتاب"
    d = dm.document
    assert (d.tg_document_id, d.file_name, d.ext, d.mime, d.size) == (
        987654321, "تفسير الطبري.PDF", ".pdf", "application/pdf", 4096,
    )
    assert dm.handle is msg.document


def test_document_without_filename_guesses_ext_from_mime(gw):
    dm = gw._to_doc_message(-1, make_message([], mime="application/epub+zip", caption=""))
    assert dm.document.file_name is None
    assert dm.document.ext == ".epub"
    assert dm.caption is None


def test_non_document_message_is_ignored(gw):
    msg = types.Message(id=1, peer_id=types.PeerChannel(channel_id=1), message="hi")
    assert gw._to_doc_message(-1, msg) is None


@pytest.mark.parametrize(
    "raised, expected",
    [
        (errors.FloodWaitError(None, capture=42), FloodWait),
        (errors.FloodPremiumWaitError(None, capture=7), FloodWait),
        (errors.FileReferenceExpiredError(None), FileRefExpired),
        (errors.AuthKeyUnregisteredError(None), NotAuthorized),
        (errors.SessionRevokedError(None), NotAuthorized),
        (errors.ChannelPrivateError(None), ChatUnavailable),
        (ConnectionError("down"), NotConnected),
    ],
)
async def test_error_translation(gw, raised, expected):
    with pytest.raises(expected) as info:
        async with gw._translate():
            raise raised
    if expected is FloodWait:
        assert info.value.seconds == raised.seconds


async def test_unrelated_errors_pass_through(gw):
    with pytest.raises(ValueError):
        async with gw._translate():
            raise ValueError("x")

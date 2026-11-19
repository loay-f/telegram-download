"""Checks the real Telethon adapter against genuine Telethon objects (no network)."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from telethon import errors
from telethon.tl import types

from tglib.engine.errors import ChatUnavailable, FileRefExpired, FloodWait, NotAuthorized, NotConnected
from tglib.engine.telegram import SCAN_REQUEST_INTERVAL, DocMessage, DocumentInfo, TelethonGateway


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


async def test_dialog_kinds(gw, monkeypatch):
    """Private chats, bots and Saved Messages are listed alongside channels."""
    me = types.User(id=1, is_self=True, first_name="Me")
    bot = types.User(id=2, bot=True, first_name="Books Bot", username="booksbot")
    friend = types.User(id=3, first_name="صديق")
    gone = types.User(id=4, deleted=True)
    channel = types.Channel(id=5, title="قناة", photo=types.ChatPhotoEmpty(), date=None, broadcast=True)
    group = types.Channel(id=6, title="مجموعة", photo=types.ChatPhotoEmpty(), date=None, megagroup=True)
    dialogs = [SimpleNamespace(id=e.id, name=getattr(e, "title", None) or getattr(e, "first_name", None), entity=e)
               for e in (me, bot, friend, gone, channel, group)]

    async def fake_iter_dialogs():
        for d in dialogs:
            yield d

    monkeypatch.setattr(gw._client, "iter_dialogs", fake_iter_dialogs)

    assert [(d.title, d.type) async for d in gw.iter_dialogs()] == [
        ("الرسائل المحفوظة", "saved"),
        ("Books Bot", "bot"),
        ("صديق", "user"),
        ("حساب محذوف", "user"),
        ("قناة", "channel"),
        ("مجموعة", "group"),
    ]


# ------------------------------------------------------- reading a chat
DATE = datetime(2024, 5, 1, 12, 30, tzinfo=timezone.utc)


def make_photo():
    return types.Photo(
        id=1, access_hash=2, file_reference=b"ref", date=DATE, dc_id=2,
        sizes=[
            types.PhotoStrippedSize(type="i", bytes=b"\x01\x02\x03"),
            types.PhotoSizeProgressive(type="y", w=1280, h=800, sizes=[100, 2000, 90000]),
            types.PhotoSize(type="m", w=320, h=200, size=1000),
            types.PhotoSize(type="x", w=800, h=500, size=5000),
        ],
    )


def make_document(attributes, mime, thumbs=None):
    return types.Document(
        id=5, access_hash=1, file_reference=b"ref", date=DATE, mime_type=mime, size=999, dc_id=1,
        attributes=attributes, thumbs=thumbs,
    )


def media_message(media, **kw):
    return types.Message(id=11, peer_id=types.PeerUser(3), date=DATE, message="", media=media, **kw)


def test_channel_post_with_formatting_reply_and_forward(gw):
    msg = types.Message(
        id=9, peer_id=types.PeerChannel(channel_id=1234), date=DATE, message="خبر مهم هنا", post=True,
        entities=[
            types.MessageEntityBold(0, 3),
            types.MessageEntityTextUrl(8, 3, "https://example.com"),
            types.MessageEntityMention(0, 1),  # not shown specially
        ],
        fwd_from=types.MessageFwdHeader(date=DATE, from_name="مصدر مخفي"),
        reply_to=types.MessageReplyHeader(reply_to_msg_id=4),
        edit_date=DATE,
    )
    m = gw._to_chat_message(-1001234, msg)
    assert (m.chat_id, m.id, m.date) == (-1001234, 9, datetime(2024, 5, 1, 12, 30))
    assert m.text == "خبر مهم هنا" and m.service is None and m.media is None
    assert [(e.type, e.offset, e.length, e.url) for e in m.entities] == [
        ("bold", 0, 3, None), ("link", 8, 3, "https://example.com"),
    ]
    assert m.sender is None  # the channel itself
    assert (m.reply_to, m.forwarded_from, m.edited, m.out) == (4, "مصدر مخفي", True, False)
    assert m.handle is None  # nothing to download


def test_group_message_names_its_sender_and_forward_origin(gw):
    author = types.User(id=7, first_name="سالم", last_name="علي")
    origin = types.Channel(id=55, title="قناة الأخبار", photo=types.ChatPhotoEmpty(), date=None, broadcast=True)
    msg = types.Message(
        id=3, peer_id=types.PeerChannel(channel_id=1234), from_id=types.PeerUser(7), date=DATE, message="مرحبا",
        fwd_from=types.MessageFwdHeader(date=DATE, from_id=types.PeerChannel(channel_id=55)),
    )
    msg._finish_init(gw._client, {7: author, -1000000000055: origin}, None)
    m = gw._to_chat_message(-1001234, msg)
    assert m.sender == "سالم علي" and m.forwarded_from == "قناة الأخبار"
    assert m.reply_to is None and m.edited is False


def test_private_and_anonymous_messages_have_no_sender_name(gw):
    friend = types.User(id=3, first_name="صديق")
    incoming = types.Message(id=1, peer_id=types.PeerUser(3), date=DATE, message="أهلاً")
    incoming._finish_init(gw._client, {3: friend}, None)
    assert gw._to_chat_message(3, incoming).sender is None  # the chat's title already says who

    group = types.Channel(id=1234, title="مجموعة", photo=types.ChatPhotoEmpty(), date=None, megagroup=True)
    anon = types.Message(id=2, peer_id=types.PeerChannel(1234), from_id=types.PeerChannel(1234), date=DATE, message="x")
    anon._finish_init(gw._client, {-1001234: group}, None)
    assert gw._to_chat_message(-1001234, anon).sender is None

    gone = types.Message(id=3, peer_id=types.PeerChannel(1234), from_id=types.PeerUser(8), date=DATE, message="x")
    gone._finish_init(gw._client, {8: types.User(id=8, deleted=True)}, None)
    assert gw._to_chat_message(-1001234, gone).sender == "حساب محذوف"


@pytest.mark.parametrize(
    "action, kind",
    [
        (types.MessageActionChatAddUser(users=[7]), "join"),
        (types.MessageActionChatJoinedByLink(inviter_id=1), "join"),
        (types.MessageActionChatDeleteUser(user_id=7), "leave"),
        (types.MessageActionPinMessage(), "pin"),
        (types.MessageActionChatEditTitle(title="جديد"), "title"),
        (types.MessageActionChannelCreate(title="قناة"), "create"),
        (types.MessageActionHistoryClear(), "other"),
    ],
)
def test_service_messages(gw, action, kind):
    msg = types.MessageService(id=3, peer_id=types.PeerChat(chat_id=5), date=DATE, action=action)
    m = gw._to_chat_message(-5, msg)
    assert m.service == kind and m.text == "" and m.media is None and m.entities == []


def test_photo(gw):
    msg = media_message(types.MessageMediaPhoto(photo=make_photo()), out=True)
    m = gw._to_chat_message(3, msg)
    assert m.out is True
    assert (m.media.kind, m.media.width, m.media.height, m.media.has_preview) == ("photo", 1280, 800, True)
    assert m.handle is msg
    assert (m.media.document.ext, m.media.size) == (".jpg", 5000)  # can be added to the library


def test_photo_as_something_to_download(gw):
    photo = make_photo()
    msg = types.Message(
        id=11, peer_id=types.PeerUser(3), date=DATE, message="تعليق", media=types.MessageMediaPhoto(photo=photo)
    )
    dm = gw._to_doc_message(3, msg)
    d = dm.document
    # Its last size is the one Telethon downloads.
    assert (d.tg_document_id, d.file_name, d.ext, d.mime, d.size) == (1, None, ".jpg", "image/jpeg", 5000)
    assert dm.handle is photo and dm.caption == "تعليق" and dm.msg_id == 11

    photo.sizes.append(types.PhotoSizeProgressive(type="w", w=2560, h=1600, sizes=[500, 9000, 400000]))
    assert gw._to_doc_message(3, media_message(types.MessageMediaPhoto(photo=photo))).document.size == 400000

    once = media_message(types.MessageMediaPhoto(photo=make_photo(), ttl_seconds=10))
    assert gw._to_doc_message(3, once) is None  # view-once stays unopened
    stripped = types.Photo(id=2, access_hash=2, file_reference=b"", date=DATE, dc_id=2,
                           sizes=[types.PhotoStrippedSize(type="i", bytes=b"\x01")])
    assert gw._to_doc_message(3, media_message(types.MessageMediaPhoto(photo=stripped))) is None


@pytest.mark.parametrize(
    "kind, wanted",
    [
        ("file", types.InputMessagesFilterDocument),
        ("media", types.InputMessagesFilterPhotoVideo),
        ("audio", types.InputMessagesFilterMusic),
    ],
)
async def test_each_kind_of_scan_asks_telegram_with_its_filter(gw, monkeypatch, kind, wanted):
    calls = []

    async def fake_input_entity(chat_id):
        return types.InputPeerChannel(1234, 1)

    async def fake_iter_messages(entity, **kw):
        calls.append(kw)
        yield media_message(types.MessageMediaPhoto(photo=make_photo()))
        yield types.Message(id=12, peer_id=types.PeerUser(3), date=DATE, message="نص فقط")

    monkeypatch.setattr(gw._client, "get_input_entity", fake_input_entity)
    monkeypatch.setattr(gw._client, "iter_messages", fake_iter_messages)
    found = [dm async for dm in gw.iter_document_messages(-1001234, 5, 40, kind)]
    assert [dm.msg_id for dm in found] == [11]
    # Paced by us: Telethon's own pause for an open-ended listing is a second.
    assert calls == [{"filter": wanted, "min_id": 5, "offset_id": 40, "wait_time": SCAN_REQUEST_INTERVAL}]


def test_view_once_photo_is_not_opened(gw):
    msg = media_message(types.MessageMediaPhoto(photo=make_photo(), ttl_seconds=10))
    m = gw._to_chat_message(3, msg)
    assert m.media.kind == "other" and m.media.has_preview is False and m.handle is None


THUMB = [types.PhotoSize(type="m", w=320, h=180, size=900)]


@pytest.mark.parametrize(
    "attributes, mime, thumbs, expected",
    [
        (  # video
            [types.DocumentAttributeVideo(duration=12.4, w=640, h=360), types.DocumentAttributeFilename("a.mp4")],
            "video/mp4", THUMB, ("video", "a.mp4", 12, 640, 360, True),
        ),
        (  # round video message
            [types.DocumentAttributeVideo(duration=5, w=240, h=240, round_message=True)],
            "video/mp4", THUMB, ("round", None, 5, 240, 240, True),
        ),
        (  # GIF
            [types.DocumentAttributeVideo(duration=2, w=100, h=80), types.DocumentAttributeAnimated()],
            "video/mp4", None, ("gif", None, 2, 100, 80, False),
        ),
        (  # voice message
            [types.DocumentAttributeAudio(duration=31, voice=True)],
            "audio/ogg", None, ("voice", None, 31, None, None, False),
        ),
        (  # music
            [types.DocumentAttributeAudio(duration=200, title="لحن", performer="فرقة")],
            "audio/mpeg", None, ("audio", "فرقة - لحن", 200, None, None, False),
        ),
        (  # sticker
            [
                types.DocumentAttributeImageSize(w=512, h=512),
                types.DocumentAttributeSticker(alt="😀", stickerset=types.InputStickerSetEmpty()),
            ],
            "image/webp", THUMB, ("sticker", "😀", None, 512, 512, True),
        ),
    ],
)
def test_document_kinds(gw, attributes, mime, thumbs, expected):
    msg = media_message(types.MessageMediaDocument(document=make_document(attributes, mime, thumbs)))
    info = gw._to_chat_message(3, msg).media
    assert (info.kind, info.name, info.duration, info.width, info.height, info.has_preview) == expected
    assert info.size == 999
    # Videos and music can be added to the library; the rest are only shown.
    assert (info.document is not None) == (info.kind in ("video", "audio"))


def test_plain_file_carries_what_the_library_needs(gw):
    doc = make_document([types.DocumentAttributeFilename("كتاب.PDF")], "application/pdf", THUMB)
    m = gw._to_chat_message(3, media_message(types.MessageMediaDocument(document=doc)))
    assert (m.media.kind, m.media.name, m.media.size) == ("file", "كتاب.PDF", 999)
    assert (m.media.document.tg_document_id, m.media.document.ext) == (5, ".pdf")
    assert m.media.has_preview is False and m.handle is None  # listed as a file


def test_other_media(gw):
    poll = types.Poll(id=1, question=types.TextWithEntities("أي كتاب؟", []), answers=[], hash=0)
    info = gw._to_chat_message(3, media_message(types.MessageMediaPoll(poll=poll, results=types.PollResults()))).media
    assert (info.kind, info.name) == ("poll", "أي كتاب؟")

    contact = types.MessageMediaContact(phone_number="+1555", first_name="منى", last_name="أحمد", vcard="", user_id=0)
    info = gw._to_chat_message(3, media_message(contact)).media
    assert (info.kind, info.name) == ("contact", "منى أحمد")

    geo = types.MessageMediaGeo(geo=types.GeoPoint(long=1.0, lat=2.0, access_hash=0))
    assert gw._to_chat_message(3, media_message(geo)).media.kind == "location"

    # A link preview is part of the text, not media.
    web = types.MessageMediaWebPage(webpage=types.WebPageEmpty(id=1))
    assert gw._to_chat_message(3, media_message(web)).media is None

    assert gw._to_chat_message(3, media_message(types.MessageMediaDice(value=3, emoticon="🎲"))).media.kind == "other"


async def test_history_is_one_request_newest_first(gw, monkeypatch):
    calls = []

    async def fake_input_entity(chat_id):
        return types.InputPeerChannel(1234, 1)

    async def fake_get_messages(entity, **kw):
        calls.append(kw)
        if "ids" in kw:
            return types.Message(id=kw["ids"], peer_id=types.PeerChannel(1234), date=DATE, message="واحدة")
        return [
            types.Message(id=i, peer_id=types.PeerChannel(1234), date=DATE, message=str(i), post=True)
            for i in (9, 8)
        ]

    monkeypatch.setattr(gw._client, "get_input_entity", fake_input_entity)
    monkeypatch.setattr(gw._client, "get_messages", fake_get_messages)

    page = await gw.get_history(-1001234, offset_id=10, min_id=3, limit=50)
    assert [(m.id, m.text) for m in page] == [(9, "9"), (8, "8")]
    assert calls == [{"limit": 50, "offset_id": 10, "min_id": 3}]
    assert (await gw.get_message(-1001234, 8)).text == "واحدة"


async def test_search_and_reading_forward_are_one_request_each(gw, monkeypatch):
    calls = []

    async def fake_input_entity(chat_id):
        return types.InputPeerChannel(1234, 1)

    async def fake_get_messages(entity, **kw):
        calls.append(kw)
        order = (11, 12) if kw.get("reverse") else (9, 8)  # Telethon returns oldest first in reverse
        return [
            types.Message(id=i, peer_id=types.PeerChannel(1234), date=DATE, message=str(i), post=True)
            for i in order
        ]

    monkeypatch.setattr(gw._client, "get_input_entity", fake_input_entity)
    monkeypatch.setattr(gw._client, "get_messages", fake_get_messages)

    found = await gw.search_history(-1001234, "كتاب", offset_id=10, limit=30)
    assert [m.id for m in found] == [9, 8]
    after = await gw.get_history_after(-1001234, 10, limit=20)
    assert [m.id for m in after] == [12, 11]  # newest first, like every page
    assert calls == [
        {"limit": 30, "search": "كتاب", "offset_id": 10},
        {"limit": 20, "offset_id": 10, "reverse": True},
    ]


@pytest.mark.parametrize("full, size", [(False, "m"), (True, "y")])
async def test_preview_picks_a_size_that_fits(gw, monkeypatch, full, size):
    asked = []

    async def fake_download_media(msg, file, thumb=None):
        asked.append((file, thumb))
        return b"jpeg-bytes"

    monkeypatch.setattr(gw._client, "download_media", fake_download_media)
    m = gw._to_chat_message(3, media_message(types.MessageMediaPhoto(photo=make_photo())))
    assert await gw.download_preview(m, full) == b"jpeg-bytes"
    assert asked == [(bytes, size)]


async def test_preview_of_a_message_without_a_picture(gw):
    m = gw._to_chat_message(3, types.Message(id=1, peer_id=types.PeerUser(3), date=DATE, message="نص"))
    assert await gw.download_preview(m, False) is None

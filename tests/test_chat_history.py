"""Reading a chat's messages: paging, new messages, pictures, files, failures."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from tglib.app.main import create_app
from tglib.engine import history
from tglib.engine.errors import FloodWait, NotConnected
from tglib.engine.telegram import MediaInfo, TextEntity
from fake_telegram import FakeFile, FakeGateway
from helpers import running_container, sync_chats
from test_auth import login, wait_for_state
from test_chats_api import wait_until

JPEG = b"\xff\xd8\xff\xe0" + b"picture" * 20
CHANNEL = -1001


def gateway(messages: int = 0) -> FakeGateway:
    gw = FakeGateway()
    gw.add_chat(CHANNEL, "قناة الكتب")
    gw.add_chat(-1002, "مجموعة", "group")
    for i in range(1, messages + 1):
        gw.say(CHANNEL, i, f"رسالة {i}")
    return gw


@pytest.fixture
def gw():
    return gateway()


@pytest.fixture
def client(settings, gw):
    with TestClient(create_app(settings, lambda: gw)) as c:
        login(c)
        wait_for_state(c, "ready")
        sync_chats(c)
        yield c


def ids(res) -> list[int]:
    return [m["id"] for m in res.json()["messages"]]


def test_pages_go_back_from_the_newest(client, gw):
    for i in range(1, 121):
        gw.say(CHANNEL, i, f"رسالة {i}")

    r = client.get(f"/api/chats/{CHANNEL}/messages")
    assert r.status_code == 200
    body = r.json()
    assert body["chat"] == {"id": CHANNEL, "title": "قناة الكتب", "username": None, "type": "channel"}
    assert ids(r) == list(range(71, 121))  # oldest first, ending at the newest
    assert body["has_more"] is True
    assert body["messages"][-1]["text"] == "رسالة 120"

    r = client.get(f"/api/chats/{CHANNEL}/messages", params={"before": 71})
    assert ids(r) == list(range(21, 71)) and r.json()["has_more"] is True
    r = client.get(f"/api/chats/{CHANNEL}/messages", params={"before": 21})
    assert ids(r) == list(range(1, 21)) and r.json()["has_more"] is False
    # One request to Telegram per page.
    assert gw.history_calls == [(CHANNEL, 0, 0, 50), (CHANNEL, 71, 0, 50), (CHANNEL, 21, 0, 50)]


def test_a_full_last_page_ending_at_the_first_message_has_no_more(client, gw):
    for i in range(1, 11):
        gw.say(CHANNEL, i, "x")
    r = client.get(f"/api/chats/{CHANNEL}/messages", params={"limit": 10})
    assert ids(r) == list(range(1, 11)) and r.json()["has_more"] is False


def test_new_messages_since_the_last_one_seen(client, gw, monkeypatch):
    monkeypatch.setattr(history, "LATEST_TTL", 0)
    for i in range(1, 6):
        gw.say(CHANNEL, i, "x")
    assert ids(client.get(f"/api/chats/{CHANNEL}/messages", params={"after": 5})) == []
    gw.say(CHANNEL, 6, "جديدة")
    gw.say(CHANNEL, 7, "أجدد")
    r = client.get(f"/api/chats/{CHANNEL}/messages", params={"after": 5})
    assert ids(r) == [6, 7] and r.json()["has_more"] is False

    # More arrived than fit in one page: the page is the newest ones, and
    # `has_more` tells the reader there is a gap before them.
    for i in range(8, 40):
        gw.say(CHANNEL, i, "x")
    r = client.get(f"/api/chats/{CHANNEL}/messages", params={"after": 7, "limit": 10})
    assert ids(r) == list(range(30, 40)) and r.json()["has_more"] is True


async def test_checking_for_new_messages_is_cheap(settings, monkeypatch):
    gw = gateway(messages=5)
    async with running_container(settings, gw) as ctx:
        monkeypatch.setattr(history, "LATEST_TTL", 0)
        # Nothing new: only the newest message is looked at.
        assert await ctx.history.page(CHANNEL, after=5) == []
        assert gw.history_calls == [(CHANNEL, 0, 0, 1)]
        # One new message right after the last seen: that look was enough.
        gw.say(CHANNEL, 6, "جديدة")
        assert [m.id for m in await ctx.history.page(CHANNEL, after=5)] == [6]
        assert gw.history_calls == [(CHANNEL, 0, 0, 1)] * 2
        # Several: the page is fetched.
        gw.say(CHANNEL, 9, "أجدد")
        assert [m.id for m in await ctx.history.page(CHANNEL, after=5)] == [9, 6]
        assert gw.history_calls[2:] == [(CHANNEL, 0, 0, 1), (CHANNEL, 0, 5, 50)]
        # An empty chat that gets its first message.
        gw.add_chat(-1003, "جديدة")
        assert await ctx.history.page(-1003) == []
        gw.say(-1003, 1, "أولى")
        assert [m.id for m in await ctx.history.page(-1003)] == [1]


def test_repeated_requests_reuse_the_page(client, gw):
    gw.say(CHANNEL, 1, "x")
    for _ in range(3):
        assert ids(client.get(f"/api/chats/{CHANNEL}/messages")) == [1]
    assert len(gw.history_calls) == 1
    # A different page is a different request.
    client.get(f"/api/chats/{CHANNEL}/messages", params={"before": 1})
    assert len(gw.history_calls) == 2


def test_message_details(client, gw):
    group = -1002
    gw.say(group, 1, service="join", sender="سالم")
    gw.say(group, 2, "مرحباً بالجميع", sender="سالم")
    gw.say(
        group, 3, "انظر الرابط هنا", out=True, reply_to=2, edited=True,
        entities=[TextEntity("bold", 0, 4), TextEntity("link", 12, 3, "https://example.com/a")],
    )
    gw.say(group, 4, "منقول", sender="منى", forwarded_from="قناة الأخبار")
    gw.say(group, 5, media=MediaInfo("voice", duration=12))
    gw.say(group, 6, media=MediaInfo("poll", name="أي كتاب نقرأ؟"))

    m = {x["id"]: x for x in client.get(f"/api/chats/{group}/messages").json()["messages"]}
    assert m[1]["service"] == "join" and m[1]["sender"] == "سالم" and m[1]["text"] == ""
    assert m[2]["sender"] == "سالم" and m[2]["out"] is False and m[2]["date"] == "2024-01-01T00:02:00"
    assert m[3]["out"] is True and m[3]["reply_to"] == 2 and m[3]["edited"] is True
    assert m[3]["entities"] == [
        {"type": "bold", "offset": 0, "length": 4, "url": None},
        {"type": "link", "offset": 12, "length": 3, "url": "https://example.com/a"},
    ]
    assert m[4]["forwarded_from"] == "قناة الأخبار" and m[2]["forwarded_from"] is None
    assert m[5]["media"] == {
        "kind": "voice", "name": None, "size": None, "duration": 12,
        "width": None, "height": None, "preview": False, "file": None,
    }
    assert m[6]["media"]["kind"] == "poll" and m[6]["media"]["name"] == "أي كتاب نقرأ؟"
    assert m[2]["media"] is None


def test_file_in_a_message_can_be_added_to_the_library(client, gw):
    gw.post(CHANNEL, 7, FakeFile(70, "كتاب نادر.pdf", b"content"), caption="وصف")
    gw.say(CHANNEL, 8, "نص فقط")

    msg = client.get(f"/api/chats/{CHANNEL}/messages").json()["messages"][0]
    assert msg["id"] == 7 and msg["text"] == "وصف"
    assert msg["media"]["kind"] == "file" and msg["media"]["name"] == "كتاب نادر.pdf"
    assert msg["media"]["size"] == 7
    assert msg["media"]["file"] == {"ext": ".pdf", "doc_id": None, "status": None, "has_file": False}

    r = client.post(f"/api/chats/{CHANNEL}/messages/7/download")
    assert r.status_code == 200 and r.json()["queued"] is True and r.json()["doc_id"]

    def downloaded():
        client.app.state.ctx.history.clear()  # skip the few seconds the page is reused
        f = client.get(f"/api/chats/{CHANNEL}/messages").json()["messages"][0]["media"]["file"]
        return f if f["has_file"] else None

    assert wait_until(downloaded)["status"] == "done"
    # Asking again doesn't queue it twice.
    again = client.post(f"/api/chats/{CHANNEL}/messages/7/download").json()
    assert again["queued"] is False and again["has_file"] is True

    r = client.post(f"/api/chats/{CHANNEL}/messages/8/download")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "no_file"


def test_pictures(client, gw):
    gw.show(CHANNEL, 1, JPEG)
    gw.say(CHANNEL, 2, "بلا صورة")
    msg = client.get(f"/api/chats/{CHANNEL}/messages").json()["messages"][0]
    assert msg["media"] == {
        "kind": "photo", "name": None, "size": None, "duration": None,
        "width": 320, "height": 200, "preview": True, "file": None,
    }

    r = client.get(f"/api/chats/{CHANNEL}/messages/1/preview")
    assert r.status_code == 200 and r.content == JPEG
    assert r.headers["content-type"] == "image/jpeg"
    assert "max-age" in r.headers["cache-control"] and "private" in r.headers["cache-control"]
    # Served from memory the second time; the full size is a separate picture.
    assert client.get(f"/api/chats/{CHANNEL}/messages/1/preview").content == JPEG
    assert gw.preview_calls == [(CHANNEL, 1, False)]
    assert client.get(f"/api/chats/{CHANNEL}/messages/1/preview", params={"full": True}).content == JPEG
    assert gw.preview_calls == [(CHANNEL, 1, False), (CHANNEL, 1, True)]
    # The message came with the page: it wasn't fetched again.
    assert gw.message_calls == []

    r = client.get(f"/api/chats/{CHANNEL}/messages/2/preview")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "no_preview"
    assert client.get(f"/api/chats/{CHANNEL}/messages/999/preview").status_code == 404


def test_picture_whose_file_reference_expired_is_fetched_again(client, gw):
    gw.show(CHANNEL, 1, JPEG)
    client.get(f"/api/chats/{CHANNEL}/messages")
    gw.ref_generation += 1  # the message from the page is stale now

    assert client.get(f"/api/chats/{CHANNEL}/messages/1/preview").content == JPEG
    assert gw.message_calls == [(CHANNEL, 1)]
    assert len(gw.preview_calls) == 2  # failed with the stale copy, worked with the fresh one


def test_picture_without_opening_the_chat_first(client, gw):
    """E.g. after a restart, with the page still open in the browser."""
    gw.show(CHANNEL, 1, JPEG)
    assert client.get(f"/api/chats/{CHANNEL}/messages/1/preview").content == JPEG
    assert gw.message_calls == [(CHANNEL, 1)]


def test_unknown_and_unavailable_chats(client, gw):
    r = client.get("/api/chats/555/messages")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found"
    assert gw.history_calls == []

    gw.inaccessible_chats.add(CHANNEL)
    r = client.get(f"/api/chats/{CHANNEL}/messages")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "chat_unavailable"
    assert client.get(f"/api/chats/{CHANNEL}/messages/1/preview").status_code == 404


def test_bad_paging_values_are_rejected(client):
    assert client.get(f"/api/chats/{CHANNEL}/messages", params={"limit": 101}).status_code == 422
    assert client.get(f"/api/chats/{CHANNEL}/messages", params={"limit": 0}).status_code == 422
    assert client.get(f"/api/chats/{CHANNEL}/messages", params={"before": -1}).status_code == 422


def test_flood_wait_is_reported_and_not_made_worse(client, gw):
    gw.say(CHANNEL, 1, "x")
    gw.flood_on_next["history"] = 40
    r = client.get(f"/api/chats/{CHANNEL}/messages")
    assert r.status_code == 429 and r.json()["detail"]["code"] == "flood"
    assert r.headers["retry-after"] == "40"
    # While Telegram wants us to wait, nothing else is sent to it.
    r = client.get(f"/api/chats/{CHANNEL}/messages", params={"before": 5})
    assert r.status_code == 429 and 0 < int(r.headers["retry-after"]) <= 40
    assert client.get(f"/api/chats/{CHANNEL}/messages/1/preview").status_code == 429
    assert len(gw.history_calls) == 1 and gw.preview_calls == [] and gw.message_calls == []


def test_reading_needs_a_connected_account(client, gw):
    gw.say(CHANNEL, 1, "x")
    assert ids(client.get(f"/api/chats/{CHANNEL}/messages")) == [1]
    client.post("/api/telegram/logout")
    r = client.get(f"/api/chats/{CHANNEL}/messages")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "telegram_not_ready"
    assert client.get(f"/api/chats/{CHANNEL}/messages/1/preview").status_code == 409
    assert client.post(f"/api/chats/{CHANNEL}/messages/1/download").status_code == 409


def test_reading_needs_the_app_password(settings, gw):
    with TestClient(create_app(settings, lambda: gw)) as c:
        assert c.get(f"/api/chats/{CHANNEL}/messages").status_code == 401
        assert c.get(f"/api/chats/{CHANNEL}/messages/1/preview").status_code == 401


# ----------------------------------------------- searching inside a chat
def test_search_finds_matching_messages_newest_first(client, gw):
    for i in range(1, 41):
        gw.say(CHANNEL, i, f"كتاب الفقه {i}" if i % 2 else f"درس {i}")
    gw.post(CHANNEL, 50, FakeFile(900, "الفقه الميسر.pdf", b"x"))  # found by its file name

    r = client.get(f"/api/chats/{CHANNEL}/messages/search", params={"q": "  الفقه ", "limit": 8})
    assert r.status_code == 200
    assert ids(r) == [50, 39, 37, 35, 33, 31, 29, 27] and r.json()["has_more"] is True
    assert r.json()["messages"][0]["media"]["name"] == "الفقه الميسر.pdf"
    assert gw.search_calls == [(CHANNEL, "الفقه", 0, 8)]

    r = client.get(f"/api/chats/{CHANNEL}/messages/search", params={"q": "الفقه", "limit": 8, "before": 27})
    assert ids(r) == [25, 23, 21, 19, 17, 15, 13, 11]
    r = client.get(f"/api/chats/{CHANNEL}/messages/search", params={"q": "الفقه", "limit": 8, "before": 11})
    assert ids(r) == [9, 7, 5, 3, 1] and r.json()["has_more"] is False

    # The same search again doesn't ask Telegram again.
    client.get(f"/api/chats/{CHANNEL}/messages/search", params={"q": "الفقه", "limit": 8})
    assert len(gw.search_calls) == 3


def test_search_with_nothing_to_find_or_nothing_to_look_for(client, gw):
    gw.say(CHANNEL, 1, "مرحبا")
    r = client.get(f"/api/chats/{CHANNEL}/messages/search", params={"q": "غير موجود"})
    assert r.json() == {"messages": [], "has_more": False}
    r = client.get(f"/api/chats/{CHANNEL}/messages/search", params={"q": "   "})
    assert r.json() == {"messages": [], "has_more": False} and len(gw.search_calls) == 1
    assert client.get(f"/api/chats/{CHANNEL}/messages/search").status_code == 422
    assert client.get(f"/api/chats/{CHANNEL}/messages/search", params={"q": "x" * 201}).status_code == 422
    assert client.get("/api/chats/-999/messages/search", params={"q": "x"}).status_code == 404


def test_search_failures_are_reported_like_reading(client, gw):
    gw.say(CHANNEL, 1, "مرحبا")
    gw.flood_on_next["search"] = 25
    r = client.get(f"/api/chats/{CHANNEL}/messages/search", params={"q": "مرحبا"})
    assert r.status_code == 429 and r.headers["retry-after"] == "25"
    # Telegram asked us to wait: reading is held back too.
    assert client.get(f"/api/chats/{CHANNEL}/messages").status_code == 429
    client.post("/api/telegram/logout")
    r = client.get(f"/api/chats/{CHANNEL}/messages/search", params={"q": "مرحبا"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "telegram_not_ready"


def test_reading_around_a_message_and_on_from_there(client, gw):
    for i in range(1, 121):
        gw.say(CHANNEL, i, f"رسالة {i}")

    # A found message opens with what surrounds it.
    r = client.get(f"/api/chats/{CHANNEL}/messages", params={"around": 60, "limit": 20})
    assert ids(r) == list(range(51, 71))
    assert r.json()["has_more"] is True and r.json()["has_newer"] is True

    # Reading on towards the newest, a page at a time.
    r = client.get(f"/api/chats/{CHANNEL}/messages", params={"after": 70, "forward": True, "limit": 30})
    assert ids(r) == list(range(71, 101)) and r.json()["has_newer"] is True
    r = client.get(f"/api/chats/{CHANNEL}/messages", params={"after": 100, "forward": True, "limit": 30})
    assert ids(r) == list(range(101, 121)) and r.json()["has_newer"] is False

    # Near either end of the chat.
    r = client.get(f"/api/chats/{CHANNEL}/messages", params={"around": 118, "limit": 20})
    assert ids(r) == list(range(109, 121)) and r.json()["has_newer"] is False
    r = client.get(f"/api/chats/{CHANNEL}/messages", params={"around": 3, "limit": 20})
    assert ids(r) == list(range(1, 14)) and r.json()["has_more"] is False

    # Ordinary pages say they end at the newest message.
    assert client.get(f"/api/chats/{CHANNEL}/messages").json()["has_newer"] is False


# ------------------------------------------------ searching every chat
def test_search_across_chats_finds_messages_and_says_where(client, gw):
    gw.say(CHANNEL, 3, "درس في أصول الفقه")
    gw.say(-1002, 8, "من يعرف كتاباً في الفقه؟", sender="خالد")
    gw.say(-1002, 9, "لا علاقة لها")
    gw.post(CHANNEL, 20, FakeFile(900, "الفقه الميسر.pdf", b"x"))

    r = client.get("/api/search/messages", params={"q": " الفقه "})
    assert r.status_code == 200
    items = r.json()["items"]
    # Newest first; the file is left to the search for files.
    assert [(m["chat"]["id"], m["id"]) for m in items] == [(-1002, 8), (CHANNEL, 3)]
    assert items[0]["chat"] == {"id": -1002, "title": "مجموعة", "type": "group"}
    assert items[0]["sender"] == "خالد" and items[0]["text"] == "من يعرف كتاباً في الفقه؟"
    assert items[0]["media"] is None
    assert r.json()["has_more"] is False
    assert [h["name"] for h in client.get("/api/search", params={"q": "الفقه"}).json()["items"]] == ["الفقه الميسر.pdf"]
    assert gw.global_search_calls == [("الفقه", 30)]

    # Asked again, Telegram isn't; a full page says there may be more.
    client.get("/api/search/messages", params={"q": "الفقه"})
    assert len(gw.global_search_calls) == 1
    r = client.get("/api/search/messages", params={"q": "الفقه", "limit": 2})
    assert [m["id"] for m in r.json()["items"]] == [8] and r.json()["has_more"] is True


def test_a_message_found_in_a_chat_not_listed_yet_can_be_opened(client, gw):
    gw.add_chat(-1009, "قناة جديدة")  # joined after the chat list was last synced
    for i in range(1, 31):
        gw.say(-1009, i, "نص فريد" if i == 12 else "x")
    assert client.get("/api/chats/-1009/messages").status_code == 404

    items = client.get("/api/search/messages", params={"q": "فريد"}).json()["items"]
    assert [(m["chat"]["id"], m["id"]) for m in items] == [(-1009, 12)]
    r = client.get("/api/chats/-1009/messages", params={"around": 12, "limit": 10})
    assert ids(r) == list(range(8, 18))
    # The same holds for a file found by the search for files.
    gw.add_chat(-1010, "قناة ملفات")
    gw.post(-1010, 4, FakeFile(901, "كتاب نادر.pdf", b"x"))
    assert len(client.get("/api/search", params={"q": "نادر"}).json()["items"]) == 1
    assert client.get("/api/chats/-1010/messages").status_code == 200


def test_search_across_chats_refuses_what_it_cannot_do(client, gw):
    assert client.get("/api/search/messages", params={"q": "x"}).status_code == 422
    assert client.get("/api/search/messages", params={"q": "xx", "limit": 101}).status_code == 422
    assert client.get("/api/search/messages", params={"q": " x "}).json() == {"items": [], "has_more": False}
    gw.flood_on_next["search"] = 30
    assert client.get("/api/search/messages", params={"q": "كتاب"}).status_code == 429
    client.post("/api/telegram/logout")
    r = client.get("/api/search/messages", params={"q": "كتاب"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "telegram_not_ready"


# --------------------------------------------------------- the service
async def test_simultaneous_requests_share_one_telegram_request(settings):
    gw = gateway(messages=3)
    gw.history_delay = 0.05
    async with running_container(settings, gw) as ctx:
        pages = await asyncio.gather(*(ctx.history.page(CHANNEL) for _ in range(5)))
        assert all([m.id for m in p] == [3, 2, 1] for p in pages)
        assert len(gw.history_calls) == 1


async def test_request_finishes_for_the_others_when_one_caller_leaves(settings):
    gw = gateway(messages=3)
    gw.history_delay = 0.05
    async with running_container(settings, gw) as ctx:
        first = asyncio.ensure_future(ctx.history.page(CHANNEL))
        second = asyncio.ensure_future(ctx.history.page(CHANNEL))
        await asyncio.sleep(0.01)
        first.cancel()
        assert [m.id for m in await second] == [3, 2, 1]
        assert len(gw.history_calls) == 1


async def test_newest_page_is_refreshed_and_older_pages_are_kept(settings, monkeypatch):
    gw = gateway(messages=3)
    async with running_container(settings, gw) as ctx:
        monkeypatch.setattr(history, "LATEST_TTL", 0)
        await ctx.history.page(CHANNEL)
        gw.say(CHANNEL, 4, "جديدة")
        assert [m.id for m in await ctx.history.page(CHANNEL)] == [4, 3, 2, 1]
        await ctx.history.page(CHANNEL, before=3)
        await ctx.history.page(CHANNEL, before=3)
        assert gw.history_calls.count((CHANNEL, 3, 0, 50)) == 1


async def test_cache_is_bounded(settings, monkeypatch):
    gw = gateway(messages=30)
    for i in range(1, 31):
        gw.show(CHANNEL, 100 + i, JPEG)
    async with running_container(settings, gw) as ctx:
        monkeypatch.setattr(history, "PAGES_MAX", 4)
        monkeypatch.setattr(history, "PREVIEWS_MAX_BYTES", len(JPEG) * 3)
        for before in range(2, 30):
            await ctx.history.page(CHANNEL, before=before, limit=1)
        assert len(ctx.history._pages) == 4
        for i in range(1, 11):
            assert await ctx.history.preview(CHANNEL, 100 + i) == JPEG
        assert len(ctx.history._previews) == 3
        assert ctx.history._previews_bytes == len(JPEG) * 3


async def test_slow_telegram_gives_up_instead_of_hanging(settings, monkeypatch):
    gw = gateway(messages=1)
    gw.history_delay = 5
    async with running_container(settings, gw) as ctx:
        monkeypatch.setattr(history, "REQUEST_TIMEOUT", 0.05)
        with pytest.raises(NotConnected):
            await ctx.history.page(CHANNEL)
        # The failure isn't cached: the next try asks again.
        gw.history_delay = 0
        assert [m.id for m in await ctx.history.page(CHANNEL)] == [1]


async def test_flood_wait_ends(settings, monkeypatch):
    gw = gateway(messages=1)
    gw.flood_on_next["history"] = 1
    async with running_container(settings, gw) as ctx:
        with pytest.raises(FloodWait):
            await ctx.history.page(CHANNEL)
        with pytest.raises(FloodWait):
            await ctx.history.page(CHANNEL)
        ctx.history._flood_until -= 5  # the wait is over
        assert [m.id for m in await ctx.history.page(CHANNEL)] == [1]
        assert len(gw.history_calls) == 2


async def test_nothing_is_kept_after_logout(settings):
    gw = gateway(messages=2)
    gw.show(CHANNEL, 3, JPEG)
    async with running_container(settings, gw) as ctx:
        await ctx.history.page(CHANNEL)
        await ctx.history.preview(CHANNEL, 3)
        ctx.history.clear()
        assert not ctx.history._pages and not ctx.history._messages and not ctx.history._previews
        assert ctx.history._previews_bytes == 0

        # A new login (maybe another account) never sees the old one's pages.
        await ctx.history.page(CHANNEL)
        ctx.account.ready_at += 1
        gw.chats[CHANNEL].talk.clear()
        assert await ctx.history.page(CHANNEL) == []

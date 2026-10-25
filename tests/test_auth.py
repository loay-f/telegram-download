import time

import pytest
from fastapi.testclient import TestClient

from tglib.app.main import create_app
from fake_telegram import VALID_CODE, FakeGateway


def wait_for_state(client: TestClient, state: str, timeout: float = 3.0) -> dict:
    end = time.monotonic() + timeout
    while True:
        s = client.get("/api/telegram/status").json()
        if s["state"] == state or time.monotonic() > end:
            return s
        time.sleep(0.05)


def login(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"password": "test-password"})
    assert r.status_code == 200, r.text


def test_app_password_protects_api(settings):
    with TestClient(create_app(settings, lambda: FakeGateway())) as client:
        assert client.get("/api/auth/status").json() == {
            "authenticated": False,
            "password_configured": True,
        }
        assert client.get("/api/telegram/status").status_code == 401
        r = client.post("/api/auth/login", json={"password": "nope"})
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "wrong_password"
        login(client)
        assert client.get("/api/auth/status").json()["authenticated"] is True
        assert client.get("/api/telegram/status").status_code == 200
        client.post("/api/auth/logout")
        assert client.get("/api/telegram/status").status_code == 401


def test_forged_cookie_rejected(settings):
    with TestClient(create_app(settings, lambda: FakeGateway())) as client:
        client.cookies.set("tglib_session", "forged.value.sig")
        assert client.get("/api/telegram/status").status_code == 401


def test_password_change_invalidates_cookie(settings):
    with TestClient(create_app(settings, lambda: FakeGateway())) as client:
        login(client)
        cookie = client.cookies.get("tglib_session")
    settings2 = settings.model_copy(update={"app_password": "another"})
    with TestClient(create_app(settings2, lambda: FakeGateway())) as client:
        client.cookies.set("tglib_session", cookie)
        assert client.get("/api/telegram/status").status_code == 401


@pytest.mark.parametrize("two_factor", [False, True])
def test_telegram_login_flow(settings, two_factor):
    gw = FakeGateway(authorized=False, password="secret" if two_factor else None)
    with TestClient(create_app(settings, lambda: gw)) as client:
        login(client)
        assert wait_for_state(client, "logged_out")["state"] == "logged_out"

        r = client.post("/api/telegram/send-code", json={"phone": "+15551234"})
        assert r.json()["state"] == "code_sent"
        assert r.json()["code"]["via"] == "app" and r.json()["code"]["next_via"] == "sms"

        r = client.post("/api/telegram/resend-code")
        assert r.json()["code"]["via"] == "sms" and r.json()["code"]["next_via"] is None

        r = client.post("/api/telegram/code", json={"code": "00000"})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "code_invalid"

        r = client.post("/api/telegram/code", json={"code": VALID_CODE})
        if two_factor:
            assert r.json()["state"] == "password_needed"
            r = client.post("/api/telegram/password", json={"password": "wrong"})
            assert r.status_code == 400
            r = client.post("/api/telegram/password", json={"password": "secret"})
        body = r.json()
        assert body["state"] == "ready"
        assert body["me"]["name"] == "Test User"

        r = client.post("/api/telegram/logout")
        assert gw.authorized is False


def test_already_authorized_session_connects_on_startup(settings):
    gw = FakeGateway(authorized=True)
    with TestClient(create_app(settings, lambda: gw)) as client:
        login(client)
        assert wait_for_state(client, "ready")["state"] == "ready"


def test_unconfigured_telegram(settings):
    with TestClient(create_app(settings)) as client:
        login(client)
        assert client.get("/api/telegram/status").json()["state"] == "unconfigured"
        r = client.post("/api/telegram/send-code", json={"phone": "+1555"})
        assert r.json()["detail"]["code"] == "unconfigured"

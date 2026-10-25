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


@pytest.mark.parametrize("two_factor", [False, True])
def test_qr_login(settings, two_factor):
    gw = FakeGateway(authorized=False, password="secret" if two_factor else None)
    gw.qr_lifetime = 1.5
    with TestClient(create_app(settings, lambda: gw)) as client:
        login(client)
        wait_for_state(client, "logged_out")
        r = client.post("/api/telegram/qr").json()
        assert r["state"] == "qr_waiting"
        assert r["qr"]["url"] == "tg://login?token=fake1" and r["qr"]["svg"].startswith("<svg")

        # An unscanned code is renewed before it expires.
        end = time.monotonic() + 5
        while client.get("/api/telegram/status").json()["qr"]["url"] == "tg://login?token=fake1":
            assert time.monotonic() < end, "QR code was never renewed"
            time.sleep(0.1)

        gw.qr_scanned.set()  # the phone scans it
        if two_factor:
            assert wait_for_state(client, "password_needed")["state"] == "password_needed"
            client.post("/api/telegram/password", json={"password": "secret"})
        s = wait_for_state(client, "ready")
        assert s["state"] == "ready" and s["qr"] is None


def test_qr_login_can_be_cancelled(settings):
    gw = FakeGateway(authorized=False)
    with TestClient(create_app(settings, lambda: gw)) as client:
        login(client)
        wait_for_state(client, "logged_out")
        client.post("/api/telegram/qr")
        assert client.post("/api/telegram/cancel").json()["state"] == "logged_out"

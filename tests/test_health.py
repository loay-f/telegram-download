from fastapi.testclient import TestClient

from tglib.app.main import create_app


def test_health_and_placeholder(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        page = client.get("/")
        assert page.status_code == 200
        assert "مكتبة" in page.text
        assert client.get("/api/does-not-exist").status_code == 404
    assert settings.db_path.exists()

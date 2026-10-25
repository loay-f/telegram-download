"""App password protection: a signed, HTTP-only session cookie."""

import hashlib
import hmac
import os
import secrets
from pathlib import Path

from itsdangerous import BadSignature, TimestampSigner

COOKIE_NAME = "tglib_session"
MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def load_secret(data_dir: Path) -> bytes:
    path = data_dir / "secret_key"
    if path.exists():
        return path.read_bytes().strip()
    key = secrets.token_hex(32).encode()
    path.write_bytes(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


class AppAuth:
    def __init__(self, secret: bytes, password: str):
        self._password = password
        self._signer = TimestampSigner(secret, salt="tglib-session")
        # Changing APP_PASSWORD invalidates every existing cookie.
        self._marker = hashlib.sha256(secret + password.encode()).hexdigest()[:24]

    @property
    def configured(self) -> bool:
        return bool(self._password)

    def check_password(self, candidate: str) -> bool:
        if not self._password:
            return False
        return hmac.compare_digest(candidate.encode(), self._password.encode())

    def issue(self) -> str:
        return self._signer.sign(self._marker).decode()

    def verify(self, token: str | None) -> bool:
        if not token or not self._password:
            return False
        try:
            value = self._signer.unsign(token, max_age=MAX_AGE)
        except BadSignature:
            return False
        return hmac.compare_digest(value, self._marker.encode())

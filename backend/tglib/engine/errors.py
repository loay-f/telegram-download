"""Telegram-independent error types.

The gateway translates Telethon exceptions into these so the scanner and the
download engine (and their tests) never import Telethon.
"""


class TelegramError(Exception):
    pass


class FloodWait(TelegramError):
    def __init__(self, seconds: int):
        super().__init__(f"Flood wait of {seconds}s")
        self.seconds = max(1, int(seconds))


class FileRefExpired(TelegramError):
    """The file reference in a message went stale; re-fetch the message."""


class ChatUnavailable(TelegramError):
    """The chat can't be accessed (left, banned, private, unknown)."""


class NotAuthorized(TelegramError):
    """The Telegram session is not (or no longer) logged in."""


class NotConnected(TelegramError):
    """No connection to Telegram right now."""


class AuthFlowError(TelegramError):
    """A user-facing login error. `code` is stable; `message` is Arabic."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class PasswordNeeded(TelegramError):
    """Two-step verification is enabled; ask for the password."""

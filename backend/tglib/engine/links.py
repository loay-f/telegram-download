"""Parsing Telegram message links."""

import re
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass
class MessageLink:
    # A public @username, or the marked id of a private channel/group.
    peer: str | int
    msg_id: int


_HOSTS = {"t.me", "telegram.me", "telegram.dog", "www.t.me"}
_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")


def parse_message_link(text: str) -> MessageLink | None:
    """Understands t.me/<username>/<id>, t.me/c/<channel>/<id>, the same with
    a topic id in between, t.me/s/... and ?single / ?comment suffixes."""
    text = text.strip()
    if not text:
        return None
    if "://" not in text:
        text = "https://" + text
    try:
        url = urlparse(text)
    except ValueError:
        return None
    if (url.hostname or "").lower() not in _HOSTS:
        return None
    parts = [p for p in url.path.split("/") if p]
    if parts and parts[0] == "s":
        parts = parts[1:]
    if len(parts) < 2 or not parts[-1].isdigit():
        return None
    msg_id = int(parts[-1])
    if parts[0] == "c":
        if len(parts) < 3 or not parts[1].isdigit():
            return None
        return MessageLink(peer=int(f"-100{parts[1]}"), msg_id=msg_id)
    if not _USERNAME.match(parts[0]):
        return None
    return MessageLink(peer=parts[0], msg_id=msg_id)

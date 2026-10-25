import pytest

from tglib.engine.links import MessageLink, parse_message_link


@pytest.mark.parametrize(
    "text, expected",
    [
        ("https://t.me/kutub_pdf/1234", MessageLink("kutub_pdf", 1234)),
        ("t.me/kutub_pdf/1234?single", MessageLink("kutub_pdf", 1234)),
        ("https://t.me/s/kutub_pdf/99", MessageLink("kutub_pdf", 99)),
        ("https://telegram.me/kutub_pdf/5", MessageLink("kutub_pdf", 5)),
        ("https://t.me/c/1234567890/42", MessageLink(-1001234567890, 42)),
        ("https://t.me/c/1234567890/7/42", MessageLink(-1001234567890, 42)),  # topic
        ("https://t.me/somegroup/7/42", MessageLink("somegroup", 42)),
        ("  https://t.me/kutub_pdf/1234  ", MessageLink("kutub_pdf", 1234)),
    ],
)
def test_valid_links(text, expected):
    assert parse_message_link(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "https://example.com/kutub_pdf/1",
        "https://t.me/kutub_pdf",
        "https://t.me/+AbCdEfInvite",
        "https://t.me/c/notanumber/5",
        "https://t.me/kutub_pdf/abc",
        "not a link",
    ],
)
def test_invalid_links(text):
    assert parse_message_link(text) is None

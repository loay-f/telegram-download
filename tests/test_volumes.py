import pytest

from tglib.engine.normalize import normalize_name
from tglib.engine.volumes import parse_volume


@pytest.mark.parametrize(
    "name, series, volume",
    [
        ("تفسير الطبري ج1.pdf", "تفسير الطبري", 1),
        ("تفسير الطبري - ج 02.pdf", "تفسير الطبري", 2),
        ("تفسير_الطبري_الجزء_الثالث.pdf", "تفسير الطبري", 3),
        ("تفسير الطبري - الجزء الأول.pdf", "تفسير الطبري", 1),
        ("تفسير الطبري الجزء الحادي عشر.pdf", "تفسير الطبري", 11),
        ("البداية والنهاية المجلد 4.pdf", "البدايه والنهايه", 4),
        ("الجزء ٥ من تاريخ بغداد.pdf", "من تاريخ بغداد", 5),
        ("Ihya Ulum al-Din vol 2.pdf", "ihya ulum al din", 2),
        ("History Part 10.epub", "history", 10),
        ("سير أعلام النبلاء 7.pdf", "سير اعلام النبلاء", 7),
    ],
)
def test_volumes_detected(name, series, volume):
    assert parse_volume(normalize_name(name)) == (series, volume)


@pytest.mark.parametrize(
    "name",
    [
        "مقدمة ابن خلدون.pdf",
        "رواية 1984.pdf",  # a year-like number is not a volume
        "الجزء الأول.pdf",  # nothing left to group by
        "",
    ],
)
def test_no_volume(name):
    assert parse_volume(normalize_name(name)) == ("", None)


def test_different_spellings_share_a_series():
    a = parse_volume(normalize_name("تفسير الطبري ج1 @tafsir.pdf"))
    b = parse_volume(normalize_name("تفسير_الطبري - الجزء الثاني.pdf"))
    assert a[0] == b[0] and (a[1], b[1]) == (1, 2)

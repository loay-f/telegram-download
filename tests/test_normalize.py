import pytest

from tglib.engine.normalize import normalize_name, split_ext


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Book_Title.Part.1.pdf", "book title part 1"),
        ("Book Title [@SomeChannel].pdf", "book title"),
        ("@kutub_pdf - الكتاب.pdf", "الكتاب"),
        ("(الطبعة الثانية) كتاب.pdf", "الطبعه الثانيه كتاب"),
        ("  Many    spaces\t.epub", "many spaces"),
        ("no_extension", "no extension"),
        ("archive.tar.gz", "archive tar"),
        ("رابط t.me/somechannel كتاب.pdf", "رابط كتاب"),
    ],
)
def test_general_rules(raw, expected):
    assert normalize_name(raw) == expected


def test_arabic_alef_variants_and_ya_ta_marbuta():
    assert normalize_name("أحمد إبراهيم آدم ٱلكتاب.pdf") == "احمد ابراهيم ادم الكتاب"
    assert normalize_name("مستشفى المدينة.pdf") == normalize_name("مستشفي المدينه.pdf")


def test_arabic_tashkeel_and_tatweel_removed():
    assert normalize_name("الْكِتَابُ.pdf") == "الكتاب"
    assert normalize_name("الكـــتاب.pdf") == "الكتاب"


def test_arabic_digits_and_presentation_forms():
    assert normalize_name("الجزء ١٢.pdf") == "الجزء 12"
    # U+FEFB is the lam-alef ligature presentation form.
    assert normalize_name("ﻻمع.pdf") == normalize_name("لامع.pdf")


def test_same_book_different_uploads_normalize_equal():
    a = normalize_name("تفسير_الطبري_الجزء_الأول @tafsir_lib.pdf")
    b = normalize_name("تفسير الطبري - الجزء الاول.PDF")
    assert a == b == "تفسير الطبري الجزء الاول"


def test_empty():
    assert normalize_name(None) == ""
    assert normalize_name("") == ""


def test_split_ext():
    assert split_ext("Book.PDF") == ("Book", ".pdf")
    assert split_ext("noext") == ("noext", None)
    assert split_ext(".hidden") == (".hidden", None)
    assert split_ext("a.verylongext") == ("a.verylongext", None)

from tglib.engine.storage import chat_dir_name, file_name, is_legacy_path, unique_relpath


def test_windows_unsafe_characters_and_names():
    assert chat_dir_name('كتب: "مختارة" / PDF?', 1) == "كتب_ _مختارة_ _ PDF_"
    assert chat_dir_name("CON", 5) == "CON_"
    assert chat_dir_name("  ...  ", 7) == "chat 7"
    assert chat_dir_name("", -1001) == "chat -1001"
    assert file_name("نسخة.  ", ".pdf", "x") == "نسخة.pdf"
    assert file_name("book.PDF", ".pdf", "x") == "book.pdf"
    assert file_name(None, ".epub", "telegram-5") == "telegram-5.epub"
    assert file_name("a" * 300, ".pdf", "x") == "a" * 120 + ".pdf"
    assert file_name("evil", ".p/df", "x") == "evil"


def test_unique_relpath(tmp_path):
    (tmp_path / "library" / "قناة").mkdir(parents=True)
    assert unique_relpath(tmp_path, "قناة", "كتاب.pdf") == "library/قناة/كتاب.pdf"
    (tmp_path / "library" / "قناة" / "كتاب.pdf").write_bytes(b"1")
    (tmp_path / "library" / "قناة" / "كتاب (2).pdf").write_bytes(b"2")
    assert unique_relpath(tmp_path, "قناة", "كتاب.pdf") == "library/قناة/كتاب (3).pdf"


def test_legacy_paths():
    assert is_legacy_path("library/ab/" + "ab" * 32 + ".pdf")
    assert not is_legacy_path("library/قناة/كتاب.pdf")

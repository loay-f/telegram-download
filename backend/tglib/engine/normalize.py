"""File-name normalization used for pre-download duplicate detection."""

import re
import unicodedata

_TASHKEEL = re.compile("[ؐ-ًؚ-ٰٟۖ-ۭ]")
_TATWEEL = "ـ"
_ALEF = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه"})
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_CHANNEL_TAG = re.compile(r"@[\w.]+")
_URL = re.compile(r"(?:https?://|t\.me/)\S+")
_BRACKETS = re.compile(r"[\[\](){}<>«»【】〔〕（）]")
_SEPARATORS = re.compile(r"[_.\-–—,،؛;:|/\\+]+")
_SPACES = re.compile(r"\s+")
_EXT = re.compile(r"\.([A-Za-z0-9]{1,5})$")


def split_ext(file_name: str | None) -> tuple[str, str | None]:
    """('book.tar', '.gz') style split; ext lowercase with dot, or None."""
    if not file_name:
        return "", None
    m = _EXT.search(file_name)
    if not m or m.start() == 0:
        return file_name, None
    return file_name[: m.start()], "." + m.group(1).lower()


def normalize_arabic(text: str) -> str:
    text = _TASHKEEL.sub("", text).replace(_TATWEEL, "")
    return text.translate(_ALEF)


def fold(text: str) -> str:
    """Search folding: case, Arabic letter variants, tashkeel, digits."""
    return normalize_arabic(unicodedata.normalize("NFKC", text).lower()).translate(_DIGITS)


def normalize_name(file_name: str | None) -> str:
    """Lowercase, no extension, no @channel tags/links, no brackets,
    separators → spaces, Arabic letter variants unified, whitespace collapsed."""
    if not file_name:
        return ""
    # NFKC folds Arabic presentation forms (ﻻ, ﺍ…) and full-width characters.
    stem, _ = split_ext(unicodedata.normalize("NFKC", file_name))
    text = stem.lower()
    text = _URL.sub(" ", text)
    text = _CHANNEL_TAG.sub(" ", text)
    text = _BRACKETS.sub(" ", text)
    text = _SEPARATORS.sub(" ", text)
    text = normalize_arabic(text).translate(_DIGITS)
    return _SPACES.sub(" ", text).strip()

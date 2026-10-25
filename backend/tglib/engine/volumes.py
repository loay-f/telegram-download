"""Detects volume numbers in (normalized) file names, so the volumes of a
multi-volume work can be shown together: «تفسير الطبري ج1», «تفسير الطبري -
الجزء الثاني» -> series «تفسير الطبري», volumes 1 and 2."""

import re

_ORDINALS = {
    "الاول": 1, "اول": 1, "الثاني": 2, "ثاني": 2, "الثالث": 3, "ثالث": 3,
    "الرابع": 4, "رابع": 4, "الخامس": 5, "خامس": 5, "السادس": 6, "سادس": 6,
    "السابع": 7, "سابع": 7, "الثامن": 8, "ثامن": 8, "التاسع": 9, "تاسع": 9,
    "العاشر": 10, "عاشر": 10,
    "الحادي عشر": 11, "الثاني عشر": 12, "الثالث عشر": 13, "الرابع عشر": 14,
    "الخامس عشر": 15, "السادس عشر": 16, "السابع عشر": 17, "الثامن عشر": 18,
    "التاسع عشر": 19, "العشرون": 20,
}
# Longest first so «الحادي عشر» wins over «الحادي».
_ORDINAL_ALT = "|".join(sorted(_ORDINALS, key=len, reverse=True))

# Input is already lowercase with Arabic letters unified and digits ASCII.
_VOL_WORD = r"(?:ال)?(?:جزء|مجلد)|ج|م|vol(?:ume)?|part|pt|tome"
_NUMBERED = re.compile(rf"(?:^|\s)(?:{_VOL_WORD})\s*0*(\d{{1,3}})(?=\s|$)")
_ORDINAL = re.compile(rf"(?:^|\s)(?:ال)?(?:جزء|مجلد)\s+({_ORDINAL_ALT})(?=\s|$)")
_TRAILING = re.compile(r"\s0*(\d{1,2})$")
_SPACES = re.compile(r"\s+")


def parse_volume(normalized: str | None) -> tuple[str, int | None]:
    """(series_key, volume). series_key is '' when no volume is detected."""
    if not normalized:
        return "", None
    for pattern, to_int in (
        (_NUMBERED, int),
        (_ORDINAL, _ORDINALS.__getitem__),
        (_TRAILING, int),
    ):
        m = pattern.search(normalized)
        if m:
            volume = to_int(m.group(1))
            base = _SPACES.sub(" ", normalized[: m.start()] + " " + normalized[m.end():]).strip()
            if base and 0 < volume <= 500:
                return base, volume
    return "", None

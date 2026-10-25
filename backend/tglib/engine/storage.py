"""Where files live on disk: library/<chat name>/<file name>.

Each unique content is stored once, in the folder of the chat where it was
first posted. Names are made safe for Windows, since the storage folder is
usually a Windows drive mounted into Docker.
"""

import re
from pathlib import Path

_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SPACES = re.compile(r"\s+")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_SAFE_EXT = re.compile(r"^\.[a-z0-9]{1,10}$")

CHAT_DIR_MAX = 60
FILE_STEM_MAX = 120


def safe_component(name: str, max_len: int, fallback: str) -> str:
    name = _SPACES.sub(" ", _FORBIDDEN.sub("_", name or "")).strip()
    name = name[:max_len].rstrip(" .")
    if not name or name.lower().split(".")[0] in _RESERVED:
        name = f"{name}_" if name else fallback
    return name


def chat_dir_name(title: str | None, chat_id: int) -> str:
    return safe_component(title or "", CHAT_DIR_MAX, f"chat {chat_id}")


def file_name(original: str | None, ext: str | None, fallback: str) -> str:
    ext = ext if ext and _SAFE_EXT.match(ext) else ""
    stem = original or ""
    if ext and stem.lower().endswith(ext):
        stem = stem[: -len(ext)]
    return safe_component(stem, FILE_STEM_MAX, fallback) + ext


def unique_relpath(storage_dir: Path, chat_dir: str, name: str) -> str:
    """library/<chat>/<name>, with " (2)", " (3)"... if the name is taken."""
    stem, ext = Path(name).stem, Path(name).suffix
    candidate = f"library/{chat_dir}/{name}"
    n = 2
    while (storage_dir / candidate).exists():
        candidate = f"library/{chat_dir}/{stem} ({n}){ext}"
        n += 1
    return candidate


def is_legacy_path(rel: str) -> bool:
    """Paths from before the by-chat layout: library/ab/<sha256><ext>."""
    return re.match(r"^library/[0-9a-f]{2}/[0-9a-f]{64}(\.[a-z0-9]+)?$", rel) is not None


def remove_empty_dirs(storage_dir: Path, rel_file: str) -> None:
    """Removes now-empty parent folders up to (not including) library/."""
    root = (storage_dir / "library").resolve()
    p = (storage_dir / rel_file).resolve().parent
    while p != root and root in p.parents:
        try:
            p.rmdir()
        except OSError:
            break
        p = p.parent

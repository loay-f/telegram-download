#!/usr/bin/env python3
"""Registers files that are already on disk in the TG Library database.

One file, no dependencies besides Python 3.8+: copy it anywhere (a hard
drive, another computer) and run it there.

    python register_files.py

opens a window: choose the folders, press the button. Nothing else to know.
(It finds the library when this file is in the project's `tools` folder, or
is run from the project folder; otherwise the window asks where it is.)

The same from a command line, with more options:

    python register_files.py --db PATH/data/library.db --storage PATH/storage  FOLDER [FOLDER ...]

What it does with every file under the folders:

1. Reads its name, size, type, date and content hash (sha256).
2. Skips it if that exact content is already in the library.
3. Leaves it exactly where it is: nothing is copied, moved or renamed. The
   database only remembers where the file is. (The app opens files only
   from inside its own storage folder, so a file registered elsewhere is
   listed as downloaded but cannot be opened in the app. `--copy` or `--move`
   puts the files in <storage>/library/<folder name>/ instead.)
4. Records it as downloaded. You do not have to say which channel a file came
   from: if a scan already listed a file with the same name and size, that
   entry (with its channel and message) becomes the downloaded one. Otherwise
   the file is recorded on its own, and when a later scan finds it in a
   channel the app sees that it is already downloaded.

Close the app before running it, and start it again afterwards.
Try `--dry-run` first: it only reports what would happen.

The window may be used while the app is running on the same computer
(restart the app afterwards to refresh its counters). The app also has a page
of its own that does the same ("add files from your computer").
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import sys
import threading
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------
# Name handling. These are copies of the app's own rules (backend/tglib/engine
# normalize.py, volumes.py, storage.py): a file registered here has to get the
# same normalized name the app computes, or the app will not recognise it as
# the one it finds in a channel later.
# --------------------------------------------------------------------------
_TASHKEEL = re.compile("[ؐ-ًؚ-ٰٟۖ-ۭ]")
_TATWEEL = "ـ"
_ALEF = str.maketrans(
    {"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه"}
)
_DIGITS = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)
_CHANNEL_TAG = re.compile(r"@[\w.]+")
_URL = re.compile(r"(?:https?://|t\.me/)\S+")
_BRACKETS = re.compile("[\\[\\](){}<>\xab\xbb【】〔〕（）]")
_SEPARATORS = re.compile("[_.\\-–—,،؛;:|/\\\\+]+")
_SPACES = re.compile(r"\s+")
_EXT = re.compile(r"\.([A-Za-z0-9]{1,5})$")


def split_ext(file_name):
    if not file_name:
        return "", None
    m = _EXT.search(file_name)
    if not m or m.start() == 0:
        return file_name, None
    return file_name[: m.start()], "." + m.group(1).lower()


def normalize_arabic(text):
    return _TASHKEEL.sub("", text).replace(_TATWEEL, "").translate(_ALEF)


def fold(text):
    return normalize_arabic(unicodedata.normalize("NFKC", text).lower()).translate(_DIGITS)


def normalize_name(file_name):
    if not file_name:
        return ""
    stem, _ = split_ext(unicodedata.normalize("NFKC", file_name))
    text = stem.lower()
    text = _URL.sub(" ", text)
    text = _CHANNEL_TAG.sub(" ", text)
    text = _BRACKETS.sub(" ", text)
    text = _SEPARATORS.sub(" ", text)
    text = normalize_arabic(text).translate(_DIGITS)
    return _SPACES.sub(" ", text).strip()


_AL = "ال"
_JUZ = "جزء"
_MUJALLAD = "مجلد"
_ASHAR = " عشر"
_UNITS = [
    "اول", "ثاني", "ثالث", "رابع",
    "خامس", "سادس", "سابع", "ثامن",
    "تاسع", "عاشر",
]
_ORDINALS = {}
for _i, _w in enumerate(_UNITS, start=1):
    _ORDINALS[_AL + _w] = _i
    _ORDINALS[_w] = _i
_ORDINALS[_AL + "حادي" + _ASHAR] = 11
for _i, _w in enumerate(_UNITS[1:9], start=12):
    _ORDINALS[_AL + _w + _ASHAR] = _i
_ORDINALS[_AL + "عشرون"] = 20
_ORDINAL_ALT = "|".join(sorted(_ORDINALS, key=len, reverse=True))

_VOL_WORD = "(?:%s)?(?:%s|%s)|ج|م|vol(?:ume)?|part|pt|tome" % (_AL, _JUZ, _MUJALLAD)
_NUMBERED = re.compile(r"(?:^|\s)(?:%s)\s*0*(\d{1,3})(?=\s|$)" % _VOL_WORD)
_ORDINAL = re.compile(r"(?:^|\s)(?:%s)?(?:%s|%s)\s+(%s)(?=\s|$)" % (_AL, _JUZ, _MUJALLAD, _ORDINAL_ALT))
_TRAILING = re.compile(r"\s0*(\d{1,2})$")


def parse_volume(normalized):
    """(series_key, volume); series_key is '' when the name has no volume."""
    if not normalized:
        return "", None
    for pattern, to_int in ((_NUMBERED, int), (_ORDINAL, _ORDINALS.__getitem__), (_TRAILING, int)):
        m = pattern.search(normalized)
        if m:
            volume = to_int(m.group(1))
            base = _SPACES.sub(" ", normalized[: m.start()] + " " + normalized[m.end():]).strip()
            if base and 0 < volume <= 500:
                return base, volume
    return "", None


_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul"} | {"com%d" % i for i in range(1, 10)} | {"lpt%d" % i for i in range(1, 10)}
_SAFE_EXT = re.compile(r"^\.[a-z0-9]{1,10}$")
DIR_MAX = 60
FILE_STEM_MAX = 120


def safe_component(name, max_len, fallback):
    name = _SPACES.sub(" ", _FORBIDDEN.sub("_", name or "")).strip()
    name = name[:max_len].rstrip(" .")
    if not name or name.lower().split(".")[0] in _RESERVED:
        name = name + "_" if name else fallback
    return name


def safe_file_name(original, ext, fallback):
    """The name the app gives a file on disk (storage.file_name)."""
    ext = ext if ext and _SAFE_EXT.match(ext) else ""
    stem = original or ""
    if ext and stem.lower().endswith(ext):
        stem = stem[: -len(ext)]
    return safe_component(stem, FILE_STEM_MAX, fallback) + ext


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------
JUNK_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
JUNK_SUFFIXES = (".part", ".partial", ".crdownload", ".tmp", ".temp", ".download", ".!qb", ".aria2")
_NUMBERED_COPY = re.compile(r" \((\d{1,3})\)(?=(\.[^.]*)?$)")  # "book (2).pdf"

EXTRA_MIME = {
    ".epub": "application/epub+zip", ".mobi": "application/x-mobipocket-ebook",
    ".azw3": "application/vnd.amazon.ebook", ".djvu": "image/vnd.djvu", ".fb2": "application/x-fictionbook+xml",
    ".rar": "application/vnd.rar", ".7z": "application/x-7z-compressed", ".mkv": "video/x-matroska",
    ".webp": "image/webp", ".opus": "audio/ogg", ".m4a": "audio/mp4", ".docx":
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def is_junk(name):
    low = name.lower()
    return low.startswith(".") or low.startswith("~$") or low in JUNK_NAMES or low.endswith(JUNK_SUFFIXES)


def guess_mime(name, ext):
    return EXTRA_MIME.get(ext or "") or mimetypes.guess_type(name)[0] or "application/octet-stream"


def sha256_file(path, on_bytes=None):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(4 * 1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            if on_bytes:
                on_bytes(len(chunk))
    return h.hexdigest()


def db_time(ts=None):
    """UTC, in the text form the app stores dates in."""
    now = time.time()
    ts = now if ts is None or ts > now or ts <= 0 else ts
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")


def walk(folder, skip_dir):
    """Every regular file under `folder`, sorted, never entering `skip_dir`
    or a hidden or system folder (.cache, $RECYCLE.BIN)."""
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "$")) and Path(root, d).resolve() != skip_dir)
        for name in sorted(files):
            p = Path(root, name)
            if p.is_file() and not p.is_symlink():
                yield p


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" if unit == "B" else "%.1f %s") % (n, unit)
        n /= 1024.0


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
class RegisterError(Exception):
    """The run cannot start. `code` names the reason for a caller that has
    its own wording for it."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Stopped(Exception):
    """The caller asked the run to stop."""


class Library:
    def __init__(self, db_path, live=False):
        # live: the app is running on this database right now.
        self.live = live
        self.con = sqlite3.connect(str(db_path), timeout=60, isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys=ON")
        self.con.execute("PRAGMA busy_timeout=60000")
        self.cols = {}
        for table, needed in (
            ("blobs", {"id", "sha256", "size", "storage_path", "created_at"}),
            ("documents", {"id", "tg_document_id", "original_name", "normalized_name", "size", "status", "blob_id"}),
        ):
            cols = {r["name"] for r in self.con.execute("PRAGMA table_info(%s)" % table)}
            if not needed <= cols:
                raise RegisterError(
                    "not_library_db",
                    "This is not a TG Library database (or it was never opened by the app): "
                    "table '%s' is missing%s." % (table, "" if not cols else " columns " + ", ".join(sorted(needed - cols))),
                )
            self.cols[table] = cols

    def close(self):
        self.con.close()

    def backup(self, dest):
        out = sqlite3.connect(str(dest))
        try:
            self.con.backup(out)
        finally:
            out.close()

    def blob_paths(self):
        return {r["storage_path"]: r["size"] for r in self.con.execute("SELECT storage_path, size FROM blobs")}

    def has_blob(self, sha):
        return self.con.execute("SELECT storage_path FROM blobs WHERE sha256 = ?", (sha,)).fetchone()

    def path_taken(self, rel):
        return self.con.execute("SELECT 1 FROM blobs WHERE storage_path = ?", (rel,)).fetchone() is not None

    def known_documents(self, name, size, normalized, synthetic_id):
        """Documents without content that this file is: the ones a scan listed
        with this size and name, or the one this program recorded before."""
        rows = self.con.execute(
            "SELECT id, tg_document_id, original_name, normalized_name, ext, status FROM documents "
            "WHERE blob_id IS NULL AND (size = ? OR tg_document_id = ?)%s ORDER BY id"
            # One the running app is downloading right now is left to it: it
            # finds the same content here when it finishes.
            % (" AND status != 'downloading'" if self.live else ""),
            (size, synthetic_id),
        ).fetchall()
        plain = _NUMBERED_COPY.sub("", name)  # "book (2).pdf" is a second copy of "book.pdf"
        norms = {n for n in (normalized, normalize_name(plain)) if n}
        found = []
        for r in rows:
            on_disk = safe_file_name(r["original_name"], r["ext"], "telegram-%s" % r["tg_document_id"])
            if (
                r["tg_document_id"] == synthetic_id
                or (r["normalized_name"] and r["normalized_name"] in norms)
                or on_disk in (name, plain)
            ):
                found.append(r)
        return found

    def chats_of(self, doc_ids):
        if not doc_ids or "chats" not in self._tables():
            return []
        marks = ",".join("?" * len(doc_ids))
        return [
            r[0]
            for r in self.con.execute(
                "SELECT DISTINCT coalesce(c.title, c.username, c.id) FROM appearances a "
                "JOIN chats c ON c.id = a.chat_id WHERE a.document_id IN (%s)" % marks,
                doc_ids,
            )
        ]

    def _tables(self):
        return {r[0] for r in self.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def _insert(self, table, values):
        values = {k: v for k, v in values.items() if k in self.cols[table]}
        sql = "INSERT INTO %s (%s) VALUES (%s)" % (table, ", ".join(values), ", ".join("?" * len(values)))
        return self.con.execute(sql, list(values.values())).lastrowid

    def _update(self, doc_id, values):
        values = {k: v for k, v in values.items() if k in self.cols["documents"]}
        sql = "UPDATE documents SET %s WHERE id = ?" % ", ".join("%s = ?" % k for k in values)
        self.con.execute(sql, list(values.values()) + [doc_id])

    def register(self, f, rel, known):
        """One transaction: the content, and the document(s) that have it.
        False if the content got into the library meanwhile (the running app
        finished downloading the same file): nothing is recorded then."""
        now = db_time()
        self.con.execute("BEGIN IMMEDIATE")
        try:
            if self.has_blob(f["sha"]) is not None:
                self.con.execute("ROLLBACK")
                return False
            blob_id = self._insert(
                "blobs", {"sha256": f["sha"], "size": f["size"], "storage_path": rel, "created_at": now}
            )
            got = {"blob_id": blob_id, "bytes_downloaded": f["size"], "completed_at": f["when"],
                   "last_error": None, "next_attempt_at": None}
            if known:
                # The first real entry becomes the downloaded one; the others
                # are its duplicates, as the app would have decided.
                main = next((r for r in known if r["status"] != "duplicate"), known[0])
                self._update(main["id"], dict(got, status="done", duplicate_of_id=None))
                for r in known:
                    if r["id"] != main["id"]:
                        self._update(r["id"], dict(got, status="duplicate", duplicate_of_id=main["id"]))
                # Duplicates that were waiting for one of them to be downloaded.
                for r in known:
                    self.con.execute(
                        "UPDATE documents SET blob_id = ?, completed_at = ? "
                        "WHERE duplicate_of_id = ? AND blob_id IS NULL",
                        (blob_id, f["when"], r["id"]),
                    )
            else:
                series_key, volume = parse_volume(f["normalized"])
                self._insert(
                    "documents",
                    dict(
                        got, tg_document_id=f["synthetic_id"], original_name=f["name"],
                        normalized_name=f["normalized"], ext=f["ext"], mime=f["mime"], size=f["size"],
                        status="done", priority=0, series_key=series_key, volume=volume,
                        search_text=f["search_text"], attempts=0, first_seen_at=f["when"],
                    ),
                )
            self.con.execute("COMMIT")
            return True
        except BaseException:
            self.con.execute("ROLLBACK")
            raise


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------
def synthetic_id(sha):
    """Telegram's document ids are positive. A file with no known Telegram
    document gets a negative one made from its content, so it is always the
    same for the same file."""
    return -(int(sha[:15], 16) + 1)


def choose_target(storage, lib, folder_label, sub_parts, name, size, sha):
    """library/<folder>/<sub folders>/<name>, made safe for Windows drives.
    Returns (relative path, already_there)."""
    stem, ext = os.path.splitext(name)
    stem = safe_component(stem, FILE_STEM_MAX, "file")
    ext = _FORBIDDEN.sub("_", ext).rstrip(" .")
    parts = ["library", safe_component(folder_label, DIR_MAX, "imported")]
    parts += [safe_component(p, DIR_MAX, "_") for p in sub_parts]
    base = "/".join(parts)
    n = 1
    while True:
        candidate = "%s/%s%s%s" % (base, stem, "" if n == 1 else " (%d)" % n, ext)
        dest = storage / candidate
        if not dest.exists():
            return candidate, False
        # Left there by a run that was interrupted before it was recorded.
        if not lib.path_taken(candidate) and dest.is_file() and dest.stat().st_size == size and sha256_file(dest) == sha:
            return candidate, True
        n += 1


def place(src, dest, move):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if move:
        try:
            os.replace(str(src), str(dest))
            return
        except OSError:
            pass  # another drive: copy, then remove
    tmp = dest.with_name(dest.name + ".registering")
    try:
        shutil.copyfile(str(src), str(tmp))
        try:
            shutil.copystat(str(src), str(tmp))
        except OSError:
            pass
        os.replace(str(tmp), str(dest))
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    if move:
        src.unlink()


class Report:
    """What a run tells its caller while it works. The command line prints
    (Console below); the app shows it on a page."""

    def found(self, files, nbytes):
        """The folders were listed: this much is left to read."""

    def start_file(self, n, path):
        """Reading the n-th file begins."""

    def read(self, nbytes):
        """This many more bytes were read."""

    def result(self, kind, path, detail=""):
        """kind: 'same' (content already in the library; detail = where),
        'linked' (a scan knew it; detail = the chats) or 'new'."""

    def failed(self, path, error):
        """The file was left out; `error` is the OSError or sqlite3.Error."""

    def backed_up(self, path):
        """The database was copied to `path` before its first change."""

    def stop_requested(self):
        return False


COUNTS = ("new", "linked", "already", "same", "junk", "empty", "filtered", "failed")


def run(db_path, storage, folders, mode="keep", exts=None, dry_run=False, backup=True, live=False, report=None):
    """Registers every file under `folders`.

    mode: 'keep' leaves the files where they are, 'copy' / 'move' puts them in
    <storage>/library/<folder name>/. exts: only these types ({'.pdf', ...}).
    live: the app is running on this database. Returns the counts (COUNTS),
    plus 'moved_bytes' and 'stopped'. Raises RegisterError if it cannot start.
    """
    if mode not in ("keep", "copy", "move"):
        raise ValueError(mode)
    report = report or Report()
    db_path, storage = Path(db_path), Path(storage)
    if not db_path.is_file():
        raise RegisterError("no_database", "Database not found: %s" % db_path)
    if not storage.is_dir():
        raise RegisterError("no_storage", "Storage folder not found: %s" % storage)
    storage = storage.resolve()
    folders = [Path(f) for f in folders]
    for folder in folders:
        if not folder.exists():
            raise RegisterError("not_found", "Not found: %s" % folder)
    lib = Library(db_path, live=live)
    try:
        return _run(lib, db_path, storage, folders, mode, exts, dry_run, backup, report)
    finally:
        lib.close()


def _run(lib, db_path, storage, folders, mode, wanted_exts, dry_run, backup, report):
    counts = dict.fromkeys(COUNTS, 0)
    out = dict(counts=counts, moved_bytes=0, stopped=False)
    move = mode == "move"

    # ---- 1. what is there
    stored = lib.blob_paths()
    tmp_dir = storage / "tmp"
    todo = []  # (path, top folder, size)
    seen = set()
    for folder in folders:
        top = folder.resolve()
        files = [top] if top.is_file() else walk(top, tmp_dir)
        for p in files:
            if report.stop_requested():
                out["stopped"] = True
                return out
            rp = p.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            if is_junk(p.name):
                counts["junk"] += 1
                continue
            if wanted_exts is not None and split_ext(p.name)[1] not in wanted_exts:
                counts["filtered"] += 1
                continue
            try:
                size = p.stat().st_size
            except OSError as e:
                counts["failed"] += 1
                report.failed(p, e)
                continue
            if size == 0:
                counts["empty"] += 1
                continue
            where = rp.relative_to(storage).as_posix() if storage in rp.parents else rp.as_posix()
            if stored.get(where) == size:
                counts["already"] += 1  # recorded at this very place: no need to read it again
                continue
            todo.append((rp, top if top.is_dir() else top.parent, size))
    report.found(len(todo), sum(size for _, _, size in todo))

    if todo and not dry_run:
        target = storage / "library" if (storage / "library").is_dir() else storage
        # Files left where they are put nothing in the storage folder.
        if not os.access(str(db_path), os.W_OK) or (mode != "keep" and not os.access(str(target), os.W_OK)):
            raise RegisterError(
                "no_permission",
                "No permission to write to the database or the storage folder. "
                "Run this as the user that owns them (on Linux with Docker: with sudo).",
            )

    # ---- 2. each file
    in_this_run = {}  # sha -> path, for a dry run where nothing is recorded
    backed_up = not backup

    def on_bytes(n):
        if report.stop_requested():
            raise Stopped
        report.read(n)

    for n, (path, top, size) in enumerate(todo, start=1):
        try:
            if report.stop_requested():
                raise Stopped
            report.start_file(n, path)
            st = path.stat()
            sha = sha256_file(path, on_bytes)
            existing = lib.has_blob(sha)
            if existing is not None or sha in in_this_run:
                counts["same"] += 1
                report.result("same", path, str(existing[0] if existing else in_this_run[sha]))
                continue
            name = path.name
            _, ext = split_ext(name)
            normalized = normalize_name(name)
            inside = storage in path.parents
            sub = path.parent.relative_to(top).parts if not inside else ()
            f = {
                "sha": sha, "size": st.st_size, "name": name, "ext": ext, "normalized": normalized,
                "mime": guess_mime(name, ext), "when": db_time(st.st_mtime), "synthetic_id": synthetic_id(sha),
                # Searchable by its name and by the folders it was in.
                "search_text": "\n".join(x for x in (fold(name), fold(" ".join((top.name,) + tuple(sub)))) if x),
            }
            known = lib.known_documents(name, f["size"], normalized, f["synthetic_id"])
            chats = lib.chats_of([r["id"] for r in known])
            if inside:
                rel, there = path.relative_to(storage).as_posix(), True
            elif mode == "keep":
                rel, there = path.as_posix(), True  # stays where it is; the full path is recorded
            elif dry_run:
                rel, there = "library/%s/..." % safe_component(top.name, DIR_MAX, "imported"), True
            else:
                rel, there = choose_target(storage, lib, top.name, sub, name, f["size"], sha)
            if not dry_run:
                if not backed_up:  # before the first change, and only if there is one
                    backed_up = True
                    copy = Path(str(db_path) + time.strftime(".before-register-%Y%m%d-%H%M%S"))
                    lib.backup(copy)
                    report.backed_up(copy)
                if not there:
                    place(path, storage / rel, move)
                    out["moved_bytes"] += f["size"]
                elif move and not inside:
                    path.unlink()  # an identical copy is already in place
                if not lib.register(f, rel, known):
                    counts["same"] += 1
                    report.result("same", path, "a file the app has just downloaded")
                    continue
            in_this_run[sha] = path
            if known:
                counts["linked"] += 1
                report.result("linked", path, ", ".join(str(c) for c in chats) or "an earlier entry")
            else:
                counts["new"] += 1
                report.result("new", path)
        except (KeyboardInterrupt, Stopped):
            out["stopped"] = True
            break
        except (OSError, sqlite3.Error) as e:
            counts["failed"] += 1
            report.failed(path, e)
    return out


# --------------------------------------------------------------------------
# The command line
# --------------------------------------------------------------------------
class Console(Report):
    def __init__(self, verbose):
        self.verbose = verbose
        self.quiet = not sys.stderr.isatty()
        self.files = self.n = self.done = 0
        self.bytes = 1
        self.last = 0.0

    def found(self, files, nbytes):
        self.files, self.bytes = files, max(nbytes, 1)
        print("Found %d files to look at (%s)." % (files, human(nbytes)))

    def start_file(self, n, path):
        self.n = n

    def read(self, nbytes):
        self.done += nbytes
        now = time.time()
        if not self.quiet and now - self.last > 0.25:
            self.last = now
            sys.stderr.write(
                "\r  %d/%d files, %s of %s (%d%%)   "
                % (self.n, self.files, human(self.done), human(self.bytes), 100 * self.done // self.bytes)
            )
            sys.stderr.flush()

    def clear(self):
        if not self.quiet:
            sys.stderr.write("\r" + " " * 70 + "\r")
            sys.stderr.flush()

    def say(self, mark, text, always=False):
        if self.verbose or always:
            self.clear()
            print("  %s %s" % (mark, text))

    def result(self, kind, path, detail=""):
        if kind == "same":
            self.say("=", "%s  (same content as %s)" % (path, detail))
        elif kind == "linked":
            self.say("+", "%s  -> known from: %s" % (path, detail))
        else:
            self.say("+", str(path))

    def failed(self, path, error):
        self.say("!", "%s: %s" % (path, error), always=True)

    def backed_up(self, path):
        self.say(" ", "Database copied to %s" % path, always=True)


# --------------------------------------------------------------------------
# What a window shows of a run. Used by the window below and by the app's own
# page (backend/tglib/engine/importer.py), so both say the same things.
# --------------------------------------------------------------------------
MAX_FAILURES = 100  # listed; the count is always complete
BACKUP_EVERY = 24 * 3600  # one safety copy of the database a day is enough
MAX_FOLDERS_LISTED = 2000

START_ERRORS = {
    "no_permission": "لا يملك البرنامج إذناً للكتابة في قاعدة البيانات (أو في مجلد التخزين عند النسخ والنقل).",
    "not_found": "المجلد لم يعد موجوداً.",
    "no_database": "لم نجد قاعدة بيانات المكتبة.",
    "no_storage": "لم نجد مجلد التخزين.",
    "not_library_db": "هذا الملف ليس قاعدة بيانات المكتبة، أو لم يُشغَّل البرنامج عليها من قبل.",
}


def reason(e):
    """Why a file was left out, in the user's words."""
    if isinstance(e, OSError):
        if e.errno == errno.ENOSPC:
            return "لا توجد مساحة كافية على القرص."
        if e.errno in (errno.EACCES, errno.EPERM):
            return "لا يوجد إذن لقراءة هذا الملف أو نقله."
        if e.errno == errno.ENOENT:
            return "الملف لم يعد موجوداً."
        return e.strerror or str(e)
    return str(e)


class Job(Report):
    """One run and its progress. Written by the thread that runs it, read by
    whoever shows it (plain attribute assignments)."""

    def __init__(self, folders, mode="keep", check_only=False, on_registered=None):
        self.folders = [str(f) for f in folders]
        self.mode = mode
        self.check_only = check_only
        self.state = "running"  # running | done | stopped | failed
        self.phase = "listing"  # listing | reading
        self.files_total = self.files_done = 0
        self.bytes_total = self.bytes_done = 0
        self.current = None
        self.counts = dict.fromkeys(COUNTS, 0)
        self.moved_bytes = 0
        self.failures = []
        self.error = None
        self.stop = False
        self._on_registered = on_registered
        self._told = 0.0

    def found(self, files, nbytes):
        self.files_total, self.bytes_total, self.phase = files, nbytes, "reading"

    def start_file(self, n, path):
        self.files_done, self.current = n - 1, path.name

    def read(self, nbytes):
        self.bytes_done += nbytes

    def result(self, kind, path, detail=""):
        self.counts[kind] += 1
        # Whoever listens hears about new files every few seconds.
        if self._on_registered and kind != "same" and not self.check_only and time.monotonic() - self._told > 3:
            self._told = time.monotonic()
            self._on_registered()

    def failed(self, path, error):
        self.counts["failed"] += 1
        if len(self.failures) < MAX_FAILURES:
            self.failures.append({"path": str(path), "error": reason(error)})

    def stop_requested(self):
        return self.stop

    def as_dict(self):
        return {
            "state": self.state, "phase": self.phase, "folders": self.folders, "mode": self.mode,
            "check_only": self.check_only, "files_total": self.files_total, "files_done": self.files_done,
            "bytes_total": self.bytes_total, "bytes_done": self.bytes_done, "current": self.current,
            "counts": dict(self.counts), "moved_bytes": self.moved_bytes, "failures": list(self.failures),
            "error": self.error,
        }


def backup_due(db_path):
    db_path = Path(db_path)
    newest = max((p.stat().st_mtime for p in db_path.parent.glob(db_path.name + ".before-register-*")), default=0)
    return time.time() - newest > BACKUP_EVERY


def run_job(job, db_path, storage, live=True):
    """run() for a Job: never raises, the job says how it ended."""
    try:
        out = run(db_path, storage, job.folders, mode=job.mode, dry_run=job.check_only,
                  backup=backup_due(db_path), live=live, report=job)
        job.moved_bytes = out["moved_bytes"]
        job.state = "stopped" if out["stopped"] else "done"
        if not out["stopped"]:
            job.files_done = job.files_total
    except RegisterError as e:
        job.state, job.error = "failed", START_ERRORS.get(e.code, str(e))
    except (OSError, sqlite3.Error) as e:
        job.state, job.error = "failed", reason(e)
    except Exception as e:  # the window must not stay on "running"
        job.state, job.error = "failed", "%s: %s" % (type(e).__name__, e)
    finally:
        job.current = None


class FolderError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _is_dir(p):
    try:
        return p.is_dir()
    except OSError:
        return False


def shortcuts(storage_dir=None):
    """Places to start choosing a folder from: where such files usually are,
    then the drives."""
    home = Path.home()
    places = [
        ("telegram", home / "Downloads" / "Telegram Desktop"),
        ("downloads", home / "Downloads"),
        ("desktop", home / "Desktop"),
        ("documents", home / "Documents"),
    ]
    if storage_dir is not None:
        places.append(("storage", Path(storage_dir)))
    places.append(("home", home))
    if sys.platform == "win32":
        drives = os.listdrives() if hasattr(os, "listdrives") else ["%s:\\" % c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
        places += [("drive", Path(d)) for d in drives]
    else:
        user = home.name
        for base in (Path("/media") / user, Path("/run/media") / user, Path("/media"), Path("/mnt"), Path("/Volumes")):
            if _is_dir(base):
                try:
                    places += [("drive", p) for p in sorted(base.iterdir()) if p.name != user]
                except OSError:
                    pass
        places.append(("root", Path("/")))
    out, seen = [], set()
    for kind, p in places:
        if _is_dir(p) and p not in seen:
            seen.add(p)
            out.append({"kind": kind, "name": p.name or str(p), "path": str(p)})
    return out


def list_folder(path):
    """The folders inside `path` (hidden ones left out) and how many files
    are directly in it. Raises FolderError if it cannot be opened."""
    p = Path(path)
    if not p.is_absolute() or not _is_dir(p):
        raise FolderError("not_found", "هذا المجلد غير موجود.")
    dirs, files = [], 0
    try:
        with os.scandir(str(p)) as entries:
            for e in entries:
                try:
                    if e.is_dir():
                        if not e.name.startswith((".", "$")) and len(dirs) < MAX_FOLDERS_LISTED:
                            dirs.append({"name": e.name, "path": str(p / e.name)})
                    elif e.is_file() and not is_junk(e.name):
                        files += 1
                except OSError:
                    continue
    except PermissionError:
        raise FolderError("no_permission", "لا يوجد إذن لفتح هذا المجلد.") from None
    except OSError as e:
        raise FolderError("unreadable", "تعذّر فتح المجلد: %s" % (e.strerror or e)) from None
    dirs.sort(key=lambda d: d["name"].casefold())
    return {
        "path": str(p), "name": p.name or str(p), "parent": str(p.parent) if p.parent != p else None,
        "dirs": dirs, "files": files,
    }


# --------------------------------------------------------------------------
# The window: `python register_files.py` with nothing after it. A small page
# served to this computer only, shown in its own browser window. Files are
# always left where they are; copying and moving stay on the command line.
# --------------------------------------------------------------------------
PAGE = r"""<!doctype html>
<html lang="ar" dir="rtl">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>إضافة ملفات إلى مكتبة تيليغرام</title>
<style>
  :root { --canvas:#eef1f5; --ink:#17212b; --soft:#5f6b76; --accent:#2078c5; --strong:#17609f; --tint:#e3f0fb;
          --line:#dfe3e8; --danger:#d32f2f; --danger-tint:#fdecea; --warn-tint:#fdf1dc; --warn-ink:#6f4f12; }
  * { box-sizing: border-box; }
  html { font-size: 17px; }
  body { margin:0; background:var(--canvas); color:var(--ink); line-height:1.6;
         font-family:"IBM Plex Sans Arabic","Segoe UI","Noto Sans Arabic",Tahoma,sans-serif; }
  main { max-width: 46rem; margin: 0 auto; padding: 1.5rem 1rem 3rem; }
  h1 { font-size:1.65rem; font-weight:600; margin:0 0 .4rem; }
  h2 { font-size:1.1rem; font-weight:600; margin:0 0 .8rem; }
  p { margin:.4rem 0; }
  .soft { color:var(--soft); } .small { font-size:.88rem; }
  section { background:#fff; border:1px solid var(--line); border-radius:.875rem; padding:1.1rem 1.25rem; margin-top:1.25rem; }
  button { font:inherit; font-weight:600; border:0; border-radius:.625rem; min-height:2.75rem; padding:0 1rem; cursor:pointer;
           background:var(--accent); color:#fff; }
  button:hover { background:var(--strong); }
  button.second { background:var(--tint); color:var(--strong); }
  button.quiet { background:transparent; color:var(--accent); }
  button.quiet:hover, button.second:hover { background:#d3e6f7; }
  button.big { font-size:1.08rem; min-height:3rem; padding:0 1.5rem; }
  button:disabled { opacity:.5; cursor:not-allowed; }
  button:focus-visible, input:focus-visible { outline:3px solid var(--accent); outline-offset:2px; }
  .row { display:flex; flex-wrap:wrap; align-items:center; gap:.75rem; }
  .alert { border:1px solid; border-radius:.625rem; padding:.7rem 1rem; margin:.6rem 0; }
  .alert.danger { background:var(--danger-tint); color:var(--danger); border-color:#f1b8b3; }
  .alert.warn { background:var(--warn-tint); color:var(--warn-ink); border-color:#ecd3a4; }
  .alert.info { background:var(--tint); color:var(--strong); border-color:#bcd9f3; }
  .ltr { direction:ltr; unicode-bidi:isolate; text-align:start; word-break:break-all; }
  code { display:block; direction:ltr; text-align:left; background:#17212b; color:#fff; border-radius:.5rem; padding:.6rem .8rem;
         margin:.5rem 0; font-size:.9rem; user-select:all; word-break:break-all; }
  .x { background:transparent; color:var(--soft); min-height:2.2rem; padding:0 .6rem; font-size:1.2rem; }
  .x:hover { background:var(--line); color:var(--ink); }
  .bar { height:.6rem; background:var(--tint); border-radius:1rem; overflow:hidden; }
  .bar div { height:100%; background:var(--accent); border-radius:1rem; transition:width .6s; }
  ul.lines { padding-inline-start:1.2rem; margin:.5rem 0; }
  .fails { max-height:15rem; overflow:auto; font-size:.88rem; list-style:none; padding:0; }
  .fails li { margin-bottom:.5rem; } .fails em { color:var(--danger); font-style:normal; }
  summary { cursor:pointer; color:var(--accent); font-weight:600; }
  ul.folders { list-style:none; padding:0; margin:.9rem 0 0; }
  ul.folders:empty { display:none; }
  ul.folders li { display:flex; align-items:center; gap:.6rem; background:var(--canvas); border:1px solid var(--line);
                  border-radius:.625rem; padding:.35rem .75rem; margin-bottom:.5rem; }
  ul.folders li span { flex:1; min-width:0; }
  input[type=text] { font:inherit; border:1px solid var(--line); border-radius:.625rem; min-height:2.75rem; padding:0 .75rem;
                     flex:1; min-width:0; direction:ltr; }
  .spin { display:inline-block; width:1.1rem; height:1.1rem; border:2px solid var(--accent); border-inline-end-color:transparent;
          border-radius:50%; animation:spin 1s linear infinite; vertical-align:middle; margin-inline-end:.5rem; }
  @keyframes spin { to { transform:rotate(360deg); } }
  [hidden] { display:none !important; }
</style>
<main>
  <h1>إضافة ملفات إلى مكتبة تيليغرام</h1>
  <p class="soft">عندك ملفات نزّلتها من قبل؟ عرّف المكتبة بها حتى لا ينزّلها البرنامج مرة ثانية.
    الملفات تبقى في مكانها: لا يُنسخ ولا يُنقل ولا يُغيَّر منها شيء.</p>
  <div id="app"><p class="soft"><span class="spin"></span>جارٍ التحميل…</p></div>
  <div id="form" hidden>
    <section>
      <h2><label for="path">١. الصق مسار المجلد ثم اضغط «أضف المسار»</label></h2>
      <p class="soft small">يُسجَّل كل ما في المجلد، وما في المجلدات التي بداخله أيضاً. أضف مساراً بعد الآخر لأكثر من مجلد.</p>
      <form class="row" onsubmit="event.preventDefault(); addPath()">
        <input type="text" id="path" dir="ltr" spellcheck="false" autocomplete="off" placeholder="D:\Books">
        <button id="addpath" class="second">أضف المسار</button>
      </form>
      <div id="patherror"></div>
      <ul class="folders" id="list"></ul>
    </section>
    <section>
      <h2>٢. أضف كل المجلدات إلى المكتبة</h2>
      <div class="row">
        <button id="go" class="big" disabled onclick="start(false)">أضف الكل إلى المكتبة</button>
        <button id="check" class="quiet" disabled onclick="start(true)">افحص أولاً دون تغيير شيء</button>
      </div>
      <p class="soft small" id="hint"></p>
      <p class="soft small">المكتبة: <span class="ltr" id="dbpath"></span></p>
    </section>
  </div>
</main>
<script>
"use strict";
const nf = new Intl.NumberFormat("ar-EG"), nf1 = new Intl.NumberFormat("ar-EG", {maximumFractionDigits: 1});
const num = n => nf.format(n);
function bytes(n) {
  const u = ["بايت", "ك.ب", "م.ب", "ج.ب", "ت.ب"]; let i = 0, v = n || 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return (i ? nf1.format(v) : nf.format(v)) + " " + u[i];
}
function files(n) {
  if (n === 1) return "ملف واحد";
  if (n === 2) return "ملفان";
  const m = n % 100;
  return num(n) + (m >= 3 && m <= 10 ? " ملفات" : " ملفاً");
}
function dirs(n) {
  if (n === 1) return "مجلد واحد";
  if (n === 2) return "مجلدان";
  const m = n % 100;
  return num(n) + (m >= 3 && m <= 10 ? " مجلدات" : " مجلداً");
}
const esc = s => String(s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
async function call(path, body) {
  const r = await fetch("api/" + path, body === undefined ? {} :
    {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.message || "حدث خطأ (" + r.status + ")");
  return data;
}

let state = null, folders = [], error = "", hideResult = false, lost = false;
const app = document.getElementById("app");

async function refresh() {
  try { state = await call("state"); lost = false; }
  catch (e) { lost = true; }
  render();
}
async function act(path, body) {
  error = "";
  try { state = await call(path, body); hideResult = false; }
  catch (e) { error = e.message; }
  render();
}

function render() {
  if (!state) { if (lost) app.innerHTML = '<div class="alert danger">توقف البرنامج. شغّله من جديد.</div>'; return; }
  const job = state.job, running = job && job.state === "running";
  let h = "";
  if (lost) h += '<div class="alert danger">توقف البرنامج. أغلق هذه النافذة وشغّله من جديد.</div>';
  if (state.problem) h += problem();
  if (running) h += progress(job);
  else if (job && !hideResult) h += result(job);
  if (error) h += '<div class="alert danger">' + esc(error) + "</div>";
  app.innerHTML = h;
  document.getElementById("form").hidden = running || (state.problem && state.problem !== "not_writable");
  document.getElementById("dbpath").textContent = state.db || "";
  buttons();
}

function problem() {
  if (state.problem === "not_writable")
    return '<section><h2>يلزم إذن بالكتابة أولاً</h2>' +
      '<p>قاعدة بيانات المكتبة يملكها مستخدم آخر (هذا معتاد حين يعمل البرنامج داخل Docker)، فلا يمكن تسجيل الملفات فيها.</p>' +
      (state.fix ? '<p>افتح الطرفية (Terminal) ونفّذ هذا السطر مرة واحدة فقط، ثم اضغط «أعد المحاولة»:</p><code>' + esc(state.fix) + "</code>" : "") +
      '<p class="soft small">يمكنك قبل ذلك أن تفحص المجلد لترى ما سيُضاف.</p>' +
      '<button class="second" onclick="refresh()">أعد المحاولة</button></section>';
  return '<section><h2>أين مكتبة تيليغرام؟</h2>' +
    '<p>' + (state.problem === "no_database" ? "لم نجد قاعدة بيانات المكتبة." : "لم نجد مجلد التخزين.") +
    ' اكتب مكانهما هنا (أو ضع هذا البرنامج داخل مجلد المكتبة وشغّله من هناك).</p>' +
    '<p class="small soft">ملف قاعدة البيانات، واسمه library.db داخل مجلد data</p>' +
    '<div class="row"><input type="text" id="db" value="' + esc(state.db || "") + '"></div>' +
    '<p class="small soft">مجلد التخزين (storage)</p>' +
    '<div class="row"><input type="text" id="storage" value="' + esc(state.storage || "") + '"></div>' +
    '<p><button onclick="act(\'locations\', {db: document.getElementById(\'db\').value, storage: document.getElementById(\'storage\').value})">حفظ</button></p></section>';
}

// The form is not redrawn with the rest (that happens every second), so what
// is being typed in it stays put; it changes only when a path is added or
// removed, and its buttons follow the state.
const pathBox = document.getElementById("path"), listEl = document.getElementById("list");
// Several pasted paths (one per line) each count; quotes from Windows'
// "Copy as path" are dropped.
function parsePaths(text) {
  return text.split(/\r?\n/).map(l => l.trim().replace(/^["']+|["']+$/g, "").trim()).filter(Boolean);
}
function showPathError(text) {
  document.getElementById("patherror").innerHTML = text ? '<div class="alert danger">' + esc(text) + "</div>" : "";
}
async function addPath() {
  const paths = parsePaths(pathBox.value);
  if (!paths.length) { pathBox.focus(); return; }
  const button = document.getElementById("addpath");
  button.disabled = true;
  const bad = [];
  for (const p of paths) {
    try {
      const found = await call("folders?path=" + encodeURIComponent(p));  // checks that it opens
      if (!folders.includes(found.path)) folders.push(found.path);
    } catch (e) { bad.push(p + " — " + e.message); }
  }
  button.disabled = false;
  pathBox.value = bad.length ? bad.map(b => b.split(" — ")[0]).join("\n") : "";
  showPathError(bad.join("\n"));
  drawList();
  pathBox.focus();
}
function removePath(i) { folders.splice(i, 1); drawList(); }
function drawList() {
  listEl.innerHTML = folders.map((f, i) =>
    '<li>📁 <span class="ltr">' + esc(f) + '</span><button class="x" title="إزالة" aria-label="إزالة هذا المجلد" onclick="removePath(' + i + ')">×</button></li>').join("");
  buttons();
}
function buttons() {
  const none = folders.length === 0;
  document.getElementById("go").disabled = none || (state && state.problem === "not_writable");
  document.getElementById("check").disabled = none;
  document.getElementById("hint").textContent = none ? "أضف مساراً واحداً على الأقل في الخطوة ١." :
    "في القائمة " + dirs(folders.length) + ". يقرأ البرنامج كل ملف ليتعرّف عليه، وقد يطول ذلك مع الملفات الكثيرة أو الكبيرة. اترك هذه النافذة مفتوحة حتى ينتهي.";
}
function start(check, from) {
  if (!from && !folders.length) return;
  act("start", {folders: from || folders, check_only: check});
  window.scrollTo(0, 0);
}

function progress(job) {
  const added = job.counts.new + job.counts.linked;
  let h = "<section><h2>" + (job.check_only ? "جارٍ الفحص…" : "جارٍ إضافة الملفات…") + "</h2>";
  if (job.phase === "listing") h += '<p class="soft"><span class="spin"></span>جارٍ حصر الملفات في المجلد…</p>';
  else {
    const pct = job.bytes_total ? Math.min(100, 100 * job.bytes_done / job.bytes_total) : 0;
    h += '<div class="bar" role="progressbar" aria-valuenow="' + Math.round(pct) + '"><div style="width:' + pct + '%"></div></div>' +
      "<p>الملف " + num(Math.min(job.files_done + 1, job.files_total)) + " من " + num(job.files_total) + " — " +
      bytes(job.bytes_done) + " من " + bytes(job.bytes_total) + "</p>" +
      (job.current ? '<p class="soft small"><bdi>' + esc(job.current) + "</bdi></p>" : "") +
      (added ? '<p class="soft small">' + (job.check_only ? "جديد حتى الآن: " : "أُضيف حتى الآن: ") + files(added) + "</p>" : "");
  }
  return h + '<div class="row"><button class="second" onclick="act(\'stop\', {})">إيقاف</button>' +
    '<span class="soft small">' + (job.check_only ? "" : "ما أُضيف حتى الآن يبقى محفوظاً إن أوقفت.") + "</span></div></section>";
}

function result(job) {
  if (job.state === "failed")
    return '<div class="alert danger"><strong>لم تتم الإضافة.</strong> ' + esc(job.error || "") + "</div>";
  const c = job.counts, added = c.new + c.linked, had = c.already + c.same, ignored = c.junk + c.empty, check = job.check_only;
  let title;
  if (added) title = (check ? "سيُضاف إلى المكتبة: " : "✓ أُضيف إلى المكتبة: ") + files(added);
  else if (job.state === "stopped") title = check ? "أُوقف الفحص قبل أن ينتهي" : "أُوقف قبل أن يُضاف شيء";
  else if (had) title = "لا جديد: كل الملفات هنا مسجّلة في المكتبة من قبل";
  else if (c.failed) title = "لم يُضف شيء";
  else title = "لم نجد ملفات في هذا المجلد";
  const lines = [];
  if (c.linked) lines.push("عُرفت محادثتها، لأن فحصاً سابقاً في البرنامج وجدها هناك: " + files(c.linked) + ".");
  if (c.new) lines.push("بلا محادثة معروفة الآن (تُربط بمحادثتها تلقائياً إذا وجدها فحص لاحق): " + files(c.new) + ".");
  if (had && added) lines.push("مكررة أو مسجّلة في المكتبة من قبل، فلم تُضف مرة ثانية: " + files(had) + ".");
  if (ignored) lines.push("مؤقتة أو مخفية أو فارغة، فتُجوهلت: " + files(ignored) + ".");
  let h = '<section><div class="row" style="justify-content:space-between"><h2 style="margin:0">' + title + "</h2>" +
    '<button class="x" title="إغلاق" aria-label="إغلاق النتيجة" onclick="hideResult=true;render()">×</button></div>';
  if (job.state === "stopped")
    h += '<div class="alert warn">أُوقف العمل قبل أن ينتهي' + (!check && added ? "؛ ما أُضيف محفوظ" : "") + ". ابدأ من جديد بالمجلد نفسه ليكمل ما بقي.</div>";
  if (lines.length) h += '<ul class="lines">' + lines.map(l => "<li>" + l + "</li>").join("") + "</ul>";
  if (c.failed) {
    h += '<div class="alert danger">تعذّرت ' + (check ? "قراءة " : "إضافة ") + files(c.failed) + '.</div><details><summary>عرض الملفات التي لم تنجح</summary><ul class="fails">' +
      job.failures.map(f => '<li><span class="ltr">' + esc(f.path) + "</span><br><em>" + esc(f.error) + "</em></li>").join("") +
      (c.failed > job.failures.length ? '<li class="soft">… و' + files(c.failed - job.failures.length) + " أخرى.</li>" : "") + "</ul></details>";
  }
  if (check && added && state.problem !== "not_writable")
    h += '<p><button onclick="start(false, state.job.folders)">أضفها الآن</button></p>';
  if (check) h += '<p class="soft small">كان هذا فحصاً فقط: لم يتغيّر شيء في المكتبة ولا في ملفاتك.</p>';
  else if (added) h += '<p class="soft small">الملفات بقيت في مكانها. إن كان برنامج المكتبة مفتوحاً فأعد تشغيله لتظهر الأرقام الجديدة.</p>';
  return h + "</section>";
}

// ---- keep in step with the program; tell it when the window goes away
let first = true;
(async function loop() {
  await refresh();
  if (first && state) {
    first = false;
    const j = state.job;
    if (j && (j.state !== "done" || j.check_only)) { folders = j.folders.slice(); drawList(); render(); }
  }
  setTimeout(loop, 1000);
})();
addEventListener("pagehide", () => navigator.sendBeacon("api/bye"));
</script>
</html>
"""


class Window:
    """The state behind the page: where the library is, and the one run."""

    def __init__(self, db, storage):
        self.db = Path(db) if db else None
        self.storage = Path(storage) if storage else None
        self.job = None
        self.thread = None
        self.lock = threading.Lock()
        self.seen = time.monotonic()  # when the page last asked for anything

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def problem(self):
        if self.db is None or not self.db.is_file():
            return "no_database"
        if self.storage is None or not self.storage.is_dir():
            return "no_storage"
        # The safety copy is written next to the database.
        if not os.access(str(self.db), os.W_OK) or not os.access(str(self.db.parent), os.W_OK):
            return "not_writable"
        return None

    def state(self):
        problem = self.problem()
        fix = None
        if problem == "not_writable" and os.name == "posix":
            import getpass
            import shlex

            fix = "sudo chown -R %s %s" % (shlex.quote(getpass.getuser()), shlex.quote(str(self.db.parent.resolve())))
        return {
            "job": self.job.as_dict() if self.job else None,
            "db": str(self.db) if self.db else "",
            "storage": str(self.storage) if self.storage else "",
            "problem": problem,
            "fix": fix,
        }

    def start(self, folders, check_only):
        with self.lock:
            if self.running:
                raise FolderError("busy", "هناك إضافة تعمل الآن. انتظر حتى تنتهي أو أوقفها.")
            problem = self.problem()
            if problem in ("no_database", "no_storage") or (problem and not check_only):
                raise FolderError(problem, START_ERRORS.get(problem, START_ERRORS["no_permission"]))
            if not folders:
                raise FolderError("not_found", "أضف مسار مجلد أولاً.")
            for f in folders:
                if not (isinstance(f, str) and Path(f).is_absolute() and _is_dir(Path(f))):
                    raise FolderError("not_found", "لم نجد هذا المجلد: %s — انسخ مساره الكامل، مثل D:\\Books" % f)
            self.job = Job(folders, "keep", bool(check_only))
            # live: the app may be running on the same database.
            self.thread = threading.Thread(target=run_job, args=(self.job, self.db, self.storage), daemon=True)
            self.thread.start()

    def stop(self):
        if self.job and self.running:
            self.job.stop = True


def serve(db, storage, port=0):
    """Starts the page's server on this computer only. Returns (server,
    window, url); the caller runs server.serve_forever()."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlsplit

    window = Window(db, storage)
    token = secrets.token_urlsafe(16)  # other programs and pages cannot guess the address
    prefix = "/%s/" % token

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def send(self, status, body, kind="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def route(self, body=None):
            url = urlsplit(self.path)
            host = self.headers.get("Host", "")
            if not url.path.startswith(prefix) or host.split(":")[0] not in ("127.0.0.1", "localhost"):
                return self.send(404, {"message": "Not found"})
            window.seen = time.monotonic()
            path = url.path[len(prefix):]
            try:
                if path == "" and body is None:
                    return self.send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                if path == "api/state":
                    pass
                elif path == "api/folders" and body is None:
                    where = parse_qs(url.query).get("path", [""])[0]
                    return self.send(200, list_folder(where) if where else {"shortcuts": shortcuts(window.storage)})
                elif path == "api/start" and body is not None:
                    window.start(body.get("folders"), body.get("check_only"))
                elif path == "api/stop" and body is not None:
                    window.stop()
                elif path == "api/locations" and body is not None:
                    if not window.running:
                        window.db = Path(str(body.get("db") or "").strip().strip('"')) if body.get("db") else None
                        window.storage = Path(str(body.get("storage") or "").strip().strip('"')) if body.get("storage") else None
                elif path == "api/bye" and body is not None:
                    window.seen = time.monotonic() - GONE_AFTER + 10  # a reload asks again at once
                    return self.send(200, {})
                else:
                    return self.send(404, {"message": "Not found"})
                self.send(200, window.state())
            except FolderError as e:
                self.send(400, {"code": e.code, "message": e.message})

        def do_GET(self):
            self.route()

        def do_POST(self):
            try:
                raw = self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 1 << 20))
                body = json.loads(raw.decode("utf-8")) if raw.strip() else {}
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self.send(400, {"message": "Bad request"})
            self.route(body)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server, window, "http://127.0.0.1:%d%s" % (server.server_address[1], prefix)


GONE_AFTER = 90  # seconds without a word from the page: its window was closed


def _find_browser():
    """A Chromium browser, for a window without tabs and an address bar."""
    found = []
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
            if base:
                found.append(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
                found.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    elif sys.platform == "darwin":
        found.append(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
        found.append(Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"))
    for p in found:
        if p.is_file():
            return str(p)
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "msedge", "chrome"):
        exe = shutil.which(name)
        if exe:
            return exe
    return None


def _profile_in_use(profile):
    """True while a browser has our window's profile open (the process we
    started may hand over to another one and exit)."""
    if os.name == "nt":
        try:
            with open(str(profile / "lockfile"), "a"):
                return False
        except PermissionError:
            return True
        except OSError:
            return False
    return os.path.lexists(str(profile / "SingletonLock"))


def gui(db, storage, open_window=True):
    import subprocess
    import tempfile
    import webbrowser

    server, window, url = serve(db, storage)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print("TG Library - add files. If no window opens, open this address in a browser:", flush=True)
    print("  " + url, flush=True)
    proc, profile = None, None
    if open_window:
        exe = _find_browser()
        is_root = hasattr(os, "geteuid") and os.geteuid() == 0  # Chromium refuses to run as root
        if exe and not is_root:
            profile = Path(tempfile.mkdtemp(prefix="tglibrary-register-"))
            try:
                proc = subprocess.Popen(
                    [exe, "--app=" + url, "--user-data-dir=%s" % profile, "--no-first-run", "--no-default-browser-check",
                     "--disable-sync", "--disable-background-mode", "--window-size=900,820"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except OSError:
                proc = None
        if proc is None and not is_root:
            webbrowser.open(url)
    try:
        gone_since = None
        while True:
            time.sleep(0.5)
            if proc is not None:  # our own window: open while its browser lives
                if proc.poll() is None or _profile_in_use(profile):
                    gone_since = None
                elif gone_since is None:
                    gone_since = time.monotonic()
                elif time.monotonic() - gone_since > 3:
                    break
            elif time.monotonic() - window.seen > GONE_AFTER and not window.running:
                break  # a browser tab: gone when it stopped asking (a run is left to finish)
    except KeyboardInterrupt:
        pass
    finally:
        window.stop()
        if window.thread:
            window.thread.join()
        server.shutdown()
        if profile:
            shutil.rmtree(str(profile), ignore_errors=True)
    return 0


def default_locations():
    """Where the app keeps things when the options are not given: the Windows
    desktop app's own folder, or ./data and ./storage (the Docker setup)."""
    db = storage = None
    home = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "TGLibrary"
    if (home / "data" / "library.db").is_file():
        db = home / "data" / "library.db"
        try:
            cfg = json.loads((home / "config.json").read_text(encoding="utf-8"))
            storage = Path(cfg["storage_dir"]) if cfg.get("storage_dir") else None
        except (OSError, ValueError, KeyError):
            pass
    for base in (Path.cwd(), Path(__file__).resolve().parent, Path(__file__).resolve().parent.parent):
        if db is None and (base / "data" / "library.db").is_file():
            db = base / "data" / "library.db"
            if storage is None and (base / "storage").is_dir():
                storage = base / "storage"
    return db, storage


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Register files that are already on disk in the TG Library database.",
        epilog="Without folders a window opens to choose them in. On the command line: "
        "close the app first and start it again afterwards; try --dry-run first.",
    )
    ap.add_argument("folders", nargs="*", type=Path, help="folders (or single files) to register")
    ap.add_argument("--no-window", action="store_true", help="without folders: only print the address to open in a browser")
    ap.add_argument("--db", type=Path, help="the app's database: <data folder>/library.db")
    ap.add_argument("--storage", type=Path, help="the app's storage folder (the one that contains 'library')")
    ap.add_argument("--copy", action="store_true", help="copy the files into the storage folder (default: leave them where they are)")
    ap.add_argument("--move", action="store_true", help="move the files into the storage folder")
    ap.add_argument("--ext", help="only these file types, e.g. pdf,epub,mp4 (default: everything)")
    ap.add_argument("--dry-run", action="store_true", help="only report what would be done; change nothing")
    ap.add_argument("--no-backup", action="store_true", help="do not copy the database before changing it")
    ap.add_argument("-v", "--verbose", action="store_true", help="print a line for every file")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):  # Arabic names on a Windows console
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    found_db, found_storage = default_locations()
    db_path = args.db or found_db
    storage = args.storage or found_storage
    if not args.folders:  # started with a double click: the window
        return gui(db_path, storage, open_window=not args.no_window)
    if db_path is None or not Path(db_path).is_file():
        raise SystemExit("Database not found. Give it with --db PATH/library.db (it is in the app's data folder).")
    if storage is None or not Path(storage).is_dir():
        raise SystemExit("Storage folder not found. Give it with --storage PATH (the folder that contains 'library').")
    wanted_exts = {"." + e.strip().lower().lstrip(".") for e in args.ext.split(",") if e.strip()} if args.ext else None
    mode = "move" if args.move else "copy" if args.copy else "keep"

    print("Database: %s" % db_path)
    print("Storage:  %s" % Path(storage).resolve())
    console = Console(args.verbose)
    try:
        out = run(db_path, storage, args.folders, mode=mode, exts=wanted_exts, dry_run=args.dry_run,
                  backup=not args.no_backup, report=console)
    except RegisterError as e:
        raise SystemExit(str(e))
    console.clear()
    counts = out["counts"]
    if out["stopped"]:
        print("Stopped. What was registered so far is saved; run it again to continue.")

    would = "would be " if args.dry_run else ""
    print("")
    print("%s%d registered" % (would.capitalize(), counts["new"] + counts["linked"]))
    print("   %6d matched to a file a scan had already listed (channel known)" % counts["linked"])
    print("   %6d recorded without a channel (linked when a scan finds them)" % counts["new"])
    print("Left alone:")
    print("   %6d already in the library" % (counts["already"] + counts["same"]))
    if counts["filtered"]:
        print("   %6d other file types" % counts["filtered"])
    if counts["junk"] or counts["empty"]:
        print("   %6d temporary, hidden or empty files" % (counts["junk"] + counts["empty"]))
    if counts["failed"]:
        print("   %6d FAILED (see the lines marked ! above)" % counts["failed"])
    if not args.dry_run and out["moved_bytes"]:
        print("%s %s into %s" % ("Moved" if args.move else "Copied", human(out["moved_bytes"]), Path(storage).resolve() / "library"))
    if args.dry_run:
        print("Nothing was changed (--dry-run).")
    elif counts["new"] + counts["linked"]:
        print("Start (or restart) the app to see them in the library.")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())

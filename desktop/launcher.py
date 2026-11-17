"""Windows desktop launcher for TG Library.

Runs the same FastAPI app that the Docker image runs, but inside this process,
and shows it in its own app window (Microsoft Edge "app mode", which ships with
Windows 10/11). No Docker, Python or command line is needed by the user.

- First run (or `TGLibrary.exe --setup`): a small window asks for the Telegram
  API ID / hash and the folder for downloaded books, and saves them to
  %LOCALAPPDATA%\\TGLibrary\\config.json.
- The web-UI password is generated automatically, and the app window signs in
  through a random per-launch link (/api/auth/desktop), so the user never types it.
- Closing the app window stops the server (downloads resume on the next start).
- Starting the exe again while it is running just opens another window.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "TG Library"
APP_TITLE = "مكتبة تيليغرام — TG Library"
HOME = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "TGLibrary"
CONFIG_FILE = HOME / "config.json"
RUNNING_FILE = HOME / "running.json"
DATA_DIR = HOME / "data"
BROWSER_PROFILE = HOME / "window"
LAUNCHER_LOG = HOME / "launcher.log"
DEFAULT_PORT = 8765
MUTEX_NAME = "Local\\TGLibraryDesktopInstance"

FROZEN = getattr(sys, "frozen", False)
if FROZEN:
    BUNDLE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    FRONTEND_DIR = BUNDLE / "frontend" / "dist"
    ICON = BUNDLE / "icon.ico"
else:
    REPO = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(REPO / "backend"))
    FRONTEND_DIR = REPO / "frontend" / "dist"
    ICON = Path(__file__).with_name("icon.ico")

log = logging.getLogger("launcher")


# --------------------------------------------------------------------------- utils


def _setup_launcher_logging() -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    # A windowed exe has no console: give print()/uvicorn somewhere to write.
    if sys.stdout is None or sys.stderr is None:
        stream = open(LAUNCHER_LOG, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream
    handler = logging.FileHandler(LAUNCHER_LOG, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def _message(title: str, text: str, error: bool = False) -> None:
    flags = 0x10 if error else 0x40  # MB_ICONERROR / MB_ICONINFORMATION
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, flags | 0x00010000)  # MB_SETFOREGROUND
    except Exception:  # not on Windows
        print(f"{title}: {text}", file=sys.stderr)


def _already_running() -> bool:
    """Hold a named mutex for the life of the process; True if another instance has it."""
    try:
        kernel32 = ctypes.windll.kernel32
    except AttributeError:
        return False
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    _already_running.handle = handle  # keep it alive
    return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def _free_port(preferred: int) -> int:
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return s.getsockname()[1]
    raise RuntimeError("No free local port")


def _wait_healthy(port: int, server_thread: threading.Thread, timeout: float = 90) -> bool:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/api/health"
    while time.monotonic() < deadline:
        if not server_thread.is_alive():
            return False
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(0.3)
    return False


# -------------------------------------------------------------------------- config


def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CONFIG_FILE)


def config_complete(cfg: dict) -> bool:
    return bool(cfg.get("tg_api_id") and cfg.get("tg_api_hash") and cfg.get("storage_dir"))


def run_setup_window(cfg: dict) -> dict | None:
    """Ask for the Telegram API keys and the books folder. Returns None if cancelled."""
    import tkinter as tk
    from tkinter import filedialog, ttk

    result: dict | None = None
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on high-DPI screens
    except Exception:
        pass
    root = tk.Tk()
    root.title(f"{APP_NAME} — الإعداد / Setup")
    root.resizable(False, False)
    if ICON.exists():
        try:
            root.iconbitmap(str(ICON))
        except tk.TclError:
            pass

    frm = ttk.Frame(root, padding=18)
    frm.grid(sticky="nsew")
    font = ("Segoe UI", 10)
    bold = ("Segoe UI", 12, "bold")

    ttk.Label(frm, text="مرحباً بك في مكتبة تيليغرام", font=bold, anchor="e").grid(
        row=0, column=0, columnspan=3, sticky="ew"
    )
    ttk.Label(
        frm,
        font=font,
        justify="left",
        text=(
            "Welcome! This is needed only once.\n"
            "1. Open my.telegram.org, sign in with your phone number,\n"
            "   choose 'API development tools' and create an app (any name).\n"
            "2. Copy 'App api_id' and 'App api_hash' below. Do not share them."
        ),
    ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 4))
    link = ttk.Label(frm, text="https://my.telegram.org", foreground="#0a66c2", cursor="hand2", font=font)
    link.grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 12))
    link.bind("<Button-1>", lambda _e: webbrowser.open("https://my.telegram.org"))

    api_id = tk.StringVar(value=str(cfg.get("tg_api_id") or ""))
    api_hash = tk.StringVar(value=cfg.get("tg_api_hash") or "")
    default_storage = Path.home() / "Documents" / "TGLibrary"
    storage = tk.StringVar(value=cfg.get("storage_dir") or str(default_storage))

    ttk.Label(frm, text="App api_id:", font=font).grid(row=3, column=0, sticky="w", pady=3)
    ttk.Entry(frm, textvariable=api_id, width=48).grid(row=3, column=1, columnspan=2, sticky="ew")
    ttk.Label(frm, text="App api_hash:", font=font).grid(row=4, column=0, sticky="w", pady=3)
    ttk.Entry(frm, textvariable=api_hash, width=48).grid(row=4, column=1, columnspan=2, sticky="ew")

    ttk.Label(frm, text="Books folder / مجلد الكتب:", font=font).grid(row=5, column=0, sticky="w", pady=3)
    ttk.Entry(frm, textvariable=storage, width=38).grid(row=5, column=1, sticky="ew")

    def browse():
        chosen = filedialog.askdirectory(initialdir=storage.get() or str(Path.home()), parent=root)
        if chosen:
            storage.set(str(Path(chosen)))

    ttk.Button(frm, text="Browse… / اختر", command=browse).grid(row=5, column=2, padx=(6, 0))
    ttk.Label(
        frm,
        font=("Segoe UI", 9),
        foreground="#555",
        text="Pick a drive with plenty of free space (books can take hundreds of GB).",
    ).grid(row=6, column=0, columnspan=3, sticky="w")

    error = ttk.Label(frm, text="", foreground="#b00020", font=font)
    error.grid(row=7, column=0, columnspan=3, sticky="w", pady=(10, 0))

    def save():
        nonlocal result
        i, h, s = api_id.get().strip(), api_hash.get().strip(), storage.get().strip()
        if not i.isdigit():
            error.config(text="api_id must be a number. / يجب أن يكون api_id رقماً.")
            return
        if not re.fullmatch(r"[0-9a-fA-F]{32}", h):
            error.config(text="api_hash must be 32 letters/digits. / api_hash غير صحيح.")
            return
        if not s:
            error.config(text="Choose a books folder. / اختر مجلد الكتب.")
            return
        try:
            Path(s).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            error.config(text=f"Cannot use this folder: {exc}")
            return
        result = {**cfg, "tg_api_id": int(i), "tg_api_hash": h.lower(), "storage_dir": s}
        root.destroy()

    buttons = ttk.Frame(frm)
    buttons.grid(row=8, column=0, columnspan=3, sticky="e", pady=(14, 0))
    ttk.Button(buttons, text="Cancel / إلغاء", command=root.destroy).pack(side="right")
    ttk.Button(buttons, text="Save and start / حفظ وتشغيل", command=save).pack(side="right", padx=6)
    root.bind("<Return>", lambda _e: save())

    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_width()) // 2
    y = (root.winfo_screenheight() - root.winfo_height()) // 3
    root.geometry(f"+{x}+{y}")
    root.lift()
    root.attributes("-topmost", True)
    root.after(300, lambda: root.attributes("-topmost", False))
    root.mainloop()
    return result


# -------------------------------------------------------------------------- window


def _find_browser() -> str | None:
    candidates = []
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            candidates.append(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
            candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    for c in candidates:
        if c.is_file():
            return str(c)
    return shutil.which("msedge") or shutil.which("chrome")


def open_window(url: str) -> subprocess.Popen | None:
    """Open `url` in a chromeless app window. Returns the browser process, or None
    if it fell back to the default browser (whose lifetime we cannot track)."""
    exe = _find_browser()
    if exe:
        BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)
        args = [
            exe,
            f"--app={url}",
            f"--user-data-dir={BROWSER_PROFILE}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-sync",
            # Let the browser process exit when the window closes, so we notice.
            "--disable-background-mode",
            "--disable-features=msEdgeStartupBoost",
            "--window-size=1280,860",
        ]
        try:
            return subprocess.Popen(args, close_fds=True)
        except OSError:
            log.exception("Could not start %s", exe)
    webbrowser.open(url)
    return None


def _profile_in_use() -> bool:
    """True while any browser process has our window profile open.

    Chromium/Edge holds `<user-data-dir>/lockfile` open without sharing for its
    whole lifetime, so it cannot be opened by us until the last window closes.
    """
    lock = BROWSER_PROFILE / "lockfile"
    if not lock.exists():
        return False
    try:
        with open(lock, "a"):
            return False  # stale file left by a crash
    except PermissionError:
        return True
    except OSError:
        return False


def wait_for_window_closed(proc: subprocess.Popen, server_thread: threading.Thread) -> None:
    """Return once the app window is gone.

    The process we started is not reliable on its own: Edge may relaunch itself
    (e.g. its compatibility-layer relaunch) and exit while the real window keeps
    running. So treat the window as open while that process OR any process
    using our profile is alive, and require it to be gone for a few seconds to
    ride over the hand-off between the two.
    """
    gone_since: float | None = None
    while server_thread.is_alive():
        if proc.poll() is None or _profile_in_use():
            gone_since = None
        elif gone_since is None:
            gone_since = time.monotonic()
        elif time.monotonic() - gone_since > 5:
            return
        time.sleep(0.5)


def wait_for_quit_without_window(url: str) -> None:
    """Fallback when no Edge/Chrome: a small window that keeps the app running."""
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title(APP_NAME)
    root.resizable(False, False)
    frm = ttk.Frame(root, padding=18)
    frm.pack()
    ttk.Label(frm, text=f"{APP_NAME} is running.\nيعمل البرنامج الآن.", justify="center").pack(pady=(0, 10))
    ttk.Button(frm, text="Open / فتح", command=lambda: webbrowser.open(url)).pack(fill="x")
    ttk.Button(frm, text="Quit / إنهاء", command=root.destroy).pack(fill="x", pady=(6, 0))
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()


# -------------------------------------------------------------------------- server


def start_server(cfg: dict, port: int, token: str):
    os.environ.update(
        {
            "TG_API_ID": str(cfg["tg_api_id"]),
            "TG_API_HASH": cfg["tg_api_hash"],
            "APP_PASSWORD": cfg["app_password"],
            "DATA_DIR": str(DATA_DIR),
            "STORAGE_DIR": cfg["storage_dir"],
            "PORT": str(port),
            "DESKTOP_TOKEN": token,
            "FRONTEND_DIR": str(FRONTEND_DIR),
            "COOKIE_SECURE": "false",
        }
    )
    os.chdir(HOME)  # keep pydantic-settings from picking up a stray .env

    import uvicorn

    from tglib.app.main import app  # imported after the environment is set

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        workers=1,  # Telethon: one client per session file
        log_config=None,  # the app configures logging itself
        timeout_graceful_shutdown=10,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="server", daemon=True)
    thread.start()
    return server, thread


def main() -> int:
    _setup_launcher_logging()
    force_setup = "--setup" in sys.argv

    if _already_running():
        info = {}
        try:
            info = json.loads(RUNNING_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        if force_setup:
            _message(APP_NAME, "Close the running TG Library window first, then open Setup again.\n"
                     "أغلق نافذة البرنامج أولاً ثم افتح الإعداد من جديد.")
        elif info.get("url"):
            open_window(info["url"])
        return 0

    cfg = load_config()
    if force_setup or not config_complete(cfg):
        new = run_setup_window(cfg)
        if new is None:
            return 0
        cfg = new
    cfg.setdefault("app_password", secrets.token_urlsafe(24))
    cfg.setdefault("port", DEFAULT_PORT)
    save_config(cfg)

    port = _free_port(int(cfg.get("port") or DEFAULT_PORT))
    token = secrets.token_urlsafe(32)
    login_url = f"http://127.0.0.1:{port}/api/auth/desktop?token={token}"
    log.info("Starting on port %s (data=%s, storage=%s)", port, DATA_DIR, cfg["storage_dir"])

    server, thread = start_server(cfg, port, token)
    if not _wait_healthy(port, thread):
        log.error("Server did not start")
        server.should_exit = True
        _message(
            APP_NAME,
            "The program could not start. / تعذّر تشغيل البرنامج.\n\n"
            f"Details are in:\n{DATA_DIR / 'logs' / 'app.log'}\n{LAUNCHER_LOG}",
            error=True,
        )
        return 1

    RUNNING_FILE.write_text(json.dumps({"url": login_url, "pid": os.getpid()}), encoding="utf-8")
    try:
        proc = open_window(login_url)
        if proc is not None:
            # Wait until the user closes the app window (and any extra ones).
            wait_for_window_closed(proc, thread)
        else:
            wait_for_quit_without_window(login_url)
    finally:
        log.info("Shutting down")
        RUNNING_FILE.unlink(missing_ok=True)
        server.should_exit = True
        thread.join(timeout=30)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        text = traceback.format_exc()
        try:
            log.error(text)
        finally:
            _message(APP_NAME, f"Unexpected error / خطأ غير متوقع:\n\n{text[-1500:]}", error=True)
        sys.exit(1)

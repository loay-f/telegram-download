# PyInstaller spec for the Windows desktop build. Run via desktop/build.ps1.
# -*- mode: python -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent
BACKEND = ROOT / "backend"

datas = [
    (str(ROOT / "frontend" / "dist"), "frontend/dist"),
    # tglib/db/migrate.py looks for these next to the tglib package.
    (str(BACKEND / "alembic.ini"), "."),
    (str(BACKEND / "alembic"), "alembic"),
    (str(ROOT / "desktop" / "icon.ico"), "."),
]
binaries = []
hiddenimports = (
    collect_submodules("tglib")
    + collect_submodules("uvicorn")
    + collect_submodules("telethon")
    + collect_submodules("alembic")
    + ["aiosqlite", "sqlalchemy.dialects.sqlite.aiosqlite", "sqlalchemy.dialects.sqlite.pysqlite"]
)
for pkg in ("pymupdf", "cryptg", "segno", "tzdata"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [str(ROOT / "desktop" / "launcher.py")],
    pathex=[str(BACKEND)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "pymupdf4llm", "pymupdf_layout", "matplotlib", "numpy", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TGLibrary",
    icon=str(ROOT / "desktop" / "icon.ico"),
    console=False,  # no black console window
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="TGLibrary", upx=False)

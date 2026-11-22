# -*- mode: python ; coding: utf-8 -*-
# Build with: uv run pyinstaller swwie.spec
from PyInstaller.utils.hooks import collect_all, collect_submodules

# The .slint file is loaded at runtime, so it has to be shipped as data.
# It is placed in UI/ so Path(__file__).parent / "app.slint" keeps working.
datas = [("UI/app.slint", "UI")]
binaries = []
hiddenimports = collect_submodules("UI") + collect_submodules("fetchers")

# Slint ships a native extension, so pull in everything it needs.
for pkg in ("slint",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="swwie",
    debug=False,
    strip=False,
    upx=False,
    console=False,  # set True while debugging to see tracebacks
)

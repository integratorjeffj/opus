# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Opus.

Build with:  python3 packaging/build.py
or directly: pyinstaller packaging/opus.spec --noconfirm

Produces a single self-contained executable. The fictional sample data is
bundled inside it, so a fresh download can run the demo end to end without
touching the repository, a network, or any real order.
"""

import sys
from pathlib import Path

# SPECPATH is injected by PyInstaller and points at this file's folder.
ROOT = Path(SPECPATH).resolve().parent
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

# Sample data shipped inside the app. catalog_map.csv points at
# ../samples/catalog, so both trees have to travel together and keep their
# relative shape -- that is what makes "Try it with the sample order" work in
# a frozen build exactly as it does from a checkout.
datas = [
    (str(ROOT / "examples" / "paypal_sample.csv"), "examples"),
    (str(ROOT / "examples" / "catalog_map.csv"), "examples"),
    (str(ROOT / "examples" / "README.md"), "examples"),
    (str(ROOT / "samples" / "catalog" / "Evening Bells"),
     "samples/catalog/Evening Bells"),
    (str(ROOT / "samples" / "catalog" / "Fanfare for Two Trumpets"),
     "samples/catalog/Fanfare for Two Trumpets"),
    (str(ROOT / "LICENSE"), "."),
]

if IS_WIN:
    icon = str(ROOT / "packaging" / "opus.ico")
elif IS_MAC:
    icns = ROOT / "packaging" / "opus.icns"
    icon = str(icns) if icns.exists() else None
else:
    icon = None

a = Analysis(
    [str(ROOT / "opus.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    # cryptography is imported lazily by pypdf for AES, so PyInstaller's
    # static analysis does not always find it on its own.
    hiddenimports=["tkinter", "tkinter.ttk", "tkinter.filedialog",
                   "tkinter.messagebox", "cryptography",
                   # Adapters are imported for their registration side effect,
                   # so name them explicitly rather than trusting the graph.
                   "connectors", "connectors.base", "connectors.http",
                   "connectors.catalog_local", "connectors.catalog_gdrive",
                   "connectors.orders_paypal_csv", "connectors.orders_paypal_api",
                   "connectors.planned", "connectors.watch",
                   "connectors.confidence", "connectors.delivery_portal",
                   "connectors.delivery_smtp"],
    hookspath=[],
    runtime_hooks=[],
    # Trimmed because they are large, pulled in transitively, and unused here.
    excludes=["numpy", "matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6",
              "IPython", "pytest", "setuptools", "pip"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Opus",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,              # UPX-packed binaries trip antivirus heuristics
    runtime_tmpdir=None,
    console=False,          # windowed: double-clicking opens the app, not a terminal
    disable_windowed_traceback=False,
    argv_emulation=IS_MAC,  # lets macOS file-drops arrive as argv
    target_arch=None,
    codesign_identity=None, # signing is done after the build, see BUILDING.md
    entitlements_file=None,
    icon=icon,
    version=str(ROOT / "packaging" / "version_info.txt") if IS_WIN else None,
)

if IS_MAC:
    app = BUNDLE(
        exe,
        name="Opus.app",
        icon=icon,
        bundle_identifier="com.integratorjeffj.opus",
        info_plist={
            "CFBundleName": "Opus",
            "CFBundleDisplayName": "Opus",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "NSHumanReadableCopyright": "MIT licensed.",
        },
    )

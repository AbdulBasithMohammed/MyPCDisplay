# -*- mode: python ; coding: utf-8 -*-
"""Builds Turing Deck as a standalone Windows app.

    !!  DOES NOT RUN ON THIS MACHINE  !!

Smart App Control is enabled here (VerifiedAndReputablePolicyState = 1), which
blocks unsigned executables. The build succeeds but Windows refuses to launch
it: "An Application Control policy has blocked this file". Signing would need a
paid certificate plus established reputation.

Use tools/install_standalone.bat instead - it ships a copy of the official
Python interpreter, which keeps its Authenticode signature and is therefore
allowed. This spec is kept only as a record of the approach.

    venv/Scripts/python.exe -m PyInstaller turing-deck.spec --noconfirm

Produces dist/TuringDeck/ containing two executables:

    TuringDeck.exe      the tray controller, hotkeys and supervisor (deck.py)
    TuringDisplay.exe   renders one screen to the panel (main.py)

The controller spawns TuringDisplay.exe as its child when frozen. Neither
depends on a Python installation, so the app survives you upgrading, moving or
removing Python later.
"""
from PyInstaller.utils.hooks import collect_data_files

ICON = 'res\\icons\\deck.ico'

# res/ carries themes and fonts; config.yaml and deck.yaml are read AND written
# at runtime, so they must sit beside the exe (one-folder, contents_directory).
# external/ holds the LibreHardwareMonitor DLLs - unused with HW_SENSORS PYTHON,
# but bundled so switching the setting later does not fail with a missing file.
#
# Only the resources our screens actually use are bundled. Shipping the whole
# res/ folder would drag in ~100 upstream themes and every bundled font family:
# 1078 MB, against ~35 MB for what we reference. default.yaml is mandatory -
# config.py merges every theme against it.
#
# To add a screen later, drop its folder into res/themes/ next to the exe; the
# install directory is user-writable, so no rebuild is needed for new themes.
DATAS = [
    ('res/themes/DeckWhiteBlue', 'res/themes/DeckWhiteBlue'),
    ('res/themes/DeckDetail', 'res/themes/DeckDetail'),
    ('res/themes/default.yaml', 'res/themes'),
    ('res/fonts/jetbrains-mono', 'res/fonts/jetbrains-mono'),
    ('res/fonts/roboto', 'res/fonts/roboto'),
    ('res/fonts/malgun', 'res/fonts/malgun'),
    ('res/icons', 'res/icons'),
    ('config.yaml', '.'),
    ('deck.yaml', '.'),
    # Unused with HW_SENSORS: PYTHON, but HW_SENSORS: AUTO resolves to LHM on
    # Windows - bundled so that setting fails gracefully rather than crashing
    # on a missing DLL.
    ('external', 'external'),
]
DATAS += collect_data_files('babel')     # locale data, or date formatting breaks

# These are all imported inside functions, so PyInstaller's static analysis
# cannot see them:
#   winrt.*   -> _MediaPoller (now playing)
#   GPUtil    -> GpuVram
#   psutil    -> MemoryUsage / DiskUsage
HIDDEN = [
    'PIL', 'PIL._imagingtk', 'PIL._tkinter_finder',
    'psutil', 'GPUtil', 'ping3', 'uptime',
    'babel', 'babel.dates', 'babel.numbers', 'babel.localedata',
    'pystray._win32',
    'winrt.windows.media.control',
    'winrt.windows.foundation',
    'winrt.windows.foundation.collections',
    'winrt.windows.storage.streams',
    'winrt.system',
]

deck_a = Analysis(
    ['deck.py'],
    pathex=[],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
deck_pyz = PYZ(deck_a.pure)
deck_exe = EXE(
    deck_pyz,
    deck_a.scripts,
    [],
    exclude_binaries=True,
    name='TuringDeck',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # tray app: never show a console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[ICON],
    contents_directory='.',
)

display_a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
display_pyz = PYZ(display_a.pure)
display_exe = EXE(
    display_pyz,
    display_a.scripts,
    [],
    exclude_binaries=True,
    name='TuringDisplay',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[ICON],
    contents_directory='.',
)

coll = COLLECT(
    deck_exe, deck_a.binaries, deck_a.datas,
    display_exe, display_a.binaries, display_a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='TuringDeck',
)

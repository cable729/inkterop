# PyInstaller spec for the app sidecar: a onefile `inkterop` CLI binary
# (the Tauri shell invokes it with the `daemon` subcommand).
#
# Build via packaging/build-sidecar.sh — it places the binary at
# app/src-tauri/binaries/inkterop-daemon-<target-triple> where Tauri's
# externalBin expects it.

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

a = Analysis(
    ["entry.py"],
    pathex=[],
    binaries=[],
    # reportlab needs its font data; rmscene is pure but uses importlib
    # metadata in places; supernotelib ships palette data.
    datas=(collect_data_files("reportlab")
           + collect_data_files("supernotelib")),
    hiddenimports=(
        collect_submodules("inkterop")
        + collect_submodules("watchdog")
        + ["PIL._tkinter_finder"]
    ),
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "hypothesis"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="inkterop-daemon",
    debug=False,
    strip=False,
    upx=False,
    console=True,
    argv_emulation=False,
)

# PyInstaller spec — produces ``dist/MarketAnalytics/MarketAnalytics.exe``
# plus the matching ``_internal/`` folder.
#
# Run as:  pyinstaller scripts/MarketAnalytics.spec --noconfirm
# (build_windows.bat handles this for you).

# ruff: noqa: E501

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

# ── Hidden imports the framework can't always see ──────────────────────
#
# These show up via uvicorn → fastapi → src.api.app's lazy imports, which
# PyInstaller's static analyser misses. Listed explicitly so each module
# is bundled into _internal/ at build time.

hidden = [
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "anyio._backends._asyncio",
]

# Pull all of the project's own packages — fastest way to make sure
# every src/<package> ships.
hidden += collect_submodules("src")

# Heavy ML deps lazy-imported inside src/ — listed so PyInstaller bundles
# them even though the entrypoint doesn't reference them directly.
for pkg in (
    "sentence_transformers",
    "duckdb",
    "umap",
    "hdbscan",
    "sklearn",
    "structlog",
    "bs4",
    "py3langid",
    "jieba",
    "fugashi",
    "google_play_scraper",
    "playwright",
    "fpdf",
    "jinja2",
):
    try:
        hidden += collect_submodules(pkg)
    except Exception:
        # Optional dep; skip silently — the user gets a runtime error if
        # they invoke a feature that needs the missing module.
        pass

# Data files some packages need at runtime (model configs, .json, .so).
datas = []
for pkg in ("sentence_transformers", "umap", "hdbscan", "sklearn", "jieba",
            "playwright", "duckdb"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

# Native libs (mostly C extensions for torch, duckdb VSS, etc.)
binaries = []
for pkg in ("duckdb", "hdbscan"):
    try:
        binaries += collect_dynamic_libs(pkg)
    except Exception:
        pass

# ── App assets the launcher reaches for ──────────────────────────────
#
# build_windows.bat copies these into a sibling 'bundle/' staging dir
# right before invoking pyinstaller, so the paths here are stable.

datas += [
    # Next.js standalone server bundle (server.js + .next + node_modules
    # + .next/static + public/).
    ("../bundle/ui",   "ui"),
    # FastAPI app source (uvicorn imports src.api.app).
    ("../bundle/src",  "src"),
    # configs/ holds the YAML clustering parameters that src/pipeline
    # reads on import.
    ("../bundle/configs", "configs"),
    # Portable Node distribution (node.exe + dependencies). Optional at
    # spec-time — build_windows.bat downloads + extracts it before
    # invoking PyInstaller.
    ("../bundle/node", "node"),
]


block_cipher = None


a = Analysis(
    ["win_launcher.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Trim noise we definitely don't need on Windows.
        "tkinter",
        "test",
    ],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MarketAnalytics",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,           # Keep console so the user sees logs + Ctrl+C works
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MarketAnalytics",
)

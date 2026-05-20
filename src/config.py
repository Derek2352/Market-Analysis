"""Local config — user preferences persisted across runs.

Stores default region and other CLI defaults in ~/.mkt/config.yaml.
"""
from __future__ import annotations

from pathlib import Path
import yaml

_CONFIG_DIR = Path.home() / ".mkt"
_CONFIG_FILE = _CONFIG_DIR / "config.yaml"

_DEFAULTS: dict = {
    "default_region": "HK",
    "default_provider": "deepseek",
}


def _ensure_dir() -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """Load config, creating defaults if missing."""
    _ensure_dir()
    if not _CONFIG_FILE.exists():
        _save(_DEFAULTS.copy())
        return _DEFAULTS.copy()

    try:
        with open(_CONFIG_FILE, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        data = {}

    # Merge with defaults for missing keys
    merged = _DEFAULTS.copy()
    merged.update(data)
    return merged


def _save(data: dict) -> None:
    _ensure_dir()
    with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def get_default_region() -> str:
    """Return the user's default region code (e.g., 'HK', 'US')."""
    cfg = load_config()
    return cfg.get("default_region", "HK")


def set_default_region(region_id: str) -> None:
    """Set the user's default region."""
    cfg = load_config()
    cfg["default_region"] = region_id
    _save(cfg)

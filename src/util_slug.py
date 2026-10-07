"""Topic → filesystem slug, shared by the CLI, API and exporters.

The slug names the per-topic data directories (``data/raw/<slug>/<region>``,
``data/personas/<slug>/...``), so every reader and writer must agree on it.

Rules:
- ASCII topics slug exactly as before: lowercase, runs of anything outside
  ``[a-z0-9]`` collapse to ``_``, edges stripped ("MTR Mobile" → "mtr_mobile").
- Non-Latin letters and digits survive ("支付寶 香港" → "支付寶_香港"). The
  old ASCII-only rule turned every Chinese/Japanese topic into "untitled", so
  distinct CJK topics shared one directory and their data mixed.
- Accents on Latin letters are dropped ("Café" → "cafe") while kana voicing
  marks are kept ("が" stays "が"); full-width forms fold to ASCII via NFKC.
- Windows reserved device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9) get a
  ``_topic`` suffix, since Windows can't create those directories.
"""
from __future__ import annotations

import unicodedata

_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

# 80 code points: CJK is 3 bytes each in UTF-8, keeping names under 255 bytes.
_MAX_LEN = 80


def _strip_latin_accents(s: str) -> str:
    decomposed = unicodedata.normalize("NFKD", s)
    out: list[str] = []
    for ch in decomposed:
        # Drop a combining mark only when it decorates an ASCII letter, so
        # "é" → "e" but Japanese dakuten ("か" + ◌゙ → "が") are preserved.
        if unicodedata.category(ch) == "Mn" and out and out[-1].isascii() and out[-1].isalpha():
            continue
        out.append(ch)
    return unicodedata.normalize("NFKC", "".join(out))


def slugify(topic: str) -> str:
    s = _strip_latin_accents(topic).strip().lower()
    chars = [ch if ch.isalnum() else "_" for ch in s]
    slug = "_".join(part for part in "".join(chars).split("_") if part)
    slug = slug[:_MAX_LEN].strip("_") or "untitled"
    if slug in _WINDOWS_RESERVED:
        slug = f"{slug}_topic"
    return slug

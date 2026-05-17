from __future__ import annotations

import py3langid as langid


def detect_language(text: str) -> str | None:
    """Detect a base ISO-639-1 language code for a short text. None on too-short.

    Returns codes like 'en', 'zh', 'ja', 'ko'. Does not produce region/script
    suffixes — `language_detected` is a best-effort hint, not a precise tag.
    Downstream phases can refine with a region-aware detector if they need it
    (e.g. distinguishing zh-Hant from zh-Hans).
    """
    if not text or len(text.strip()) < 2:
        return None
    code, _ = langid.classify(text)
    return code

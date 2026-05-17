from __future__ import annotations

from src.scrape.utils.lang import detect_language


def test_detects_english() -> None:
    assert detect_language("This is a fairly clear English sentence.") == "en"


def test_detects_chinese_text() -> None:
    # Pure Traditional Chinese.
    assert detect_language(
        "這個應用程式的設計非常出色，使用者介面簡潔明瞭，功能也很豐富。"
    ) == "zh"


def test_detects_cantonese_english_mix() -> None:
    # HK-realistic code-switched review text.
    assert detect_language(
        "用咗呢個 app 好多年, 一直都好穩定, 強烈推薦比朋友使用."
    ) == "zh"


def test_short_text_returns_none() -> None:
    assert detect_language("") is None
    assert detect_language(" ") is None
    assert detect_language("a") is None


def test_does_not_raise_on_garbage() -> None:
    detect_language("!!!!!!!")
    detect_language("123 456")

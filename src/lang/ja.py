"""Japanese tokenizer — fugashi (MeCab) + UniDic for morphological analysis.

Falls back to character bigrams when fugashi is not installed (common on Windows),
which is reasonably effective for c-TF-IDF keyword extraction.
"""
from __future__ import annotations

import re

try:
    from fugashi import Tagger as _FugashiTagger
    _FUGASHI_AVAILABLE = True
except ImportError:
    _FUGASHI_AVAILABLE = False

# Common Japanese stop words and particles.
_JA_STOP_WORDS: set[str] = {
    "する", "いる", "ある", "なる", "できる", "くる", "いく", "いう",
    "の", "に", "へ", "と", "で", "が", "は", "を", "も", "か", "や",
    "から", "まで", "より", "だけ", "ほど", "ばかり", "など", "くらい",
    "こと", "もの", "よう", "そう", "ため", "わけ", "はず", "つもり",
    "これ", "それ", "あれ", "どれ", "ここ", "そこ", "あそこ", "どこ",
    "この", "その", "あの", "どの", "こんな", "そんな", "あんな",
    "どう", "こう", "そう", "ああ", "なぜ", "いつ", "どこで",
    "ない", "ます", "です", "だ", "た", "て", "られる", "させる",
    "思う", "考える", "感じる", "分かる", "出来る",
    "一", "二", "三", "十", "百", "千", "万", "円", "年", "月", "日",
    "人", "的", "性", "化", "的", "等",
    "さん", "ちゃん", "君", "様",
}

# CJK character range — used by fallback.
_CJK_RE = re.compile(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\uFF66-\uFF9F]+')


class JapaneseTokenizer:
    """Japanese tokenizer using fugashi (MeCab wrapper) + UniDic.

    When fugashi is not installed (common on Windows), falls back to
    character bigrams — less accurate than morphological analysis but still
    meaningful for c-TF-IDF keyword extraction.
    """

    def __init__(self) -> None:
        self._tagger = None
        if _FUGASHI_AVAILABLE:
            try:
                self._tagger = _FugashiTagger("-Owakati")
            except Exception:
                pass  # Fall back to character bigrams

    def tokenize(self, text: str) -> list[str]:
        """Split Japanese text into word tokens, removing stop words."""
        if self._tagger is not None:
            return self._tokenize_fugashi(text)
        return self._tokenize_bigrams(text)

    def _tokenize_fugashi(self, text: str) -> list[str]:
        tokens: list[str] = []
        for word in self._tagger(text):
            surface = word.surface
            if (
                surface
                and surface not in _JA_STOP_WORDS
                and len(surface) > 1
                and not _is_punctuation(surface)
            ):
                lemma = word.feature.lemma if hasattr(word.feature, 'lemma') else None
                tokens.append(lemma or surface)
        return tokens

    def _tokenize_bigrams(self, text: str) -> list[str]:
        """Fallback: extract CJK character bigrams.

        Splits Japanese text into overlapping 2-character sequences,
        filtering stop words and punctuation. Reasonably effective for
        keyword extraction when morphological analysis isn't available.
        """
        cjk_chars = ''.join(
            c for c in text
            if c not in _JA_STOP_WORDS and not _is_punctuation(c)
            and '\u3040' <= c <= '\u9FFF' or '\uFF66' <= c <= '\uFF9F'
        )
        tokens: list[str] = []
        for i in range(len(cjk_chars) - 1):
            bigram = cjk_chars[i:i+2]
            if bigram not in _JA_STOP_WORDS:
                tokens.append(bigram)
        # Also add individual characters for unigram coverage
        tokens.extend(
            c for c in cjk_chars
            if c not in _JA_STOP_WORDS and len(cjk_chars) > 0
        )
        return tokens


def _is_punctuation(text: str) -> bool:
    """True if *text* is only punctuation characters."""
    if not text:
        return True
    return all(
        (ord(c) < 0x3000 or (0x3000 <= ord(c) <= 0x303F and not c.isalpha()))
        and not c.isalpha()
        for c in text
    )

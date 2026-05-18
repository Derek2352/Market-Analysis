"""Japanese tokenizer — fugashi (MeCab) + UniDic for morphological analysis."""
from __future__ import annotations

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


class JapaneseTokenizer:
    """Japanese tokenizer using fugashi (MeCab wrapper) + UniDic.

    Fugashi provides lemmatised surface forms — essential for Japanese
    where words aren't space-separated. Falls back gracefully if not installed:
    raises ImportError with installation instructions.
    """

    def __init__(self) -> None:
        if not _FUGASHI_AVAILABLE:
            raise ImportError(
                "fugashi not installed. Install with: pip install fugashi unidic-lite"
            )
        try:
            self._tagger = _FugashiTagger("-Owakati")
        except Exception as e:
            raise RuntimeError(
                f"Failed to initialize fugashi Tagger: {e}. "
                "Ensure unidic-lite is installed: pip install unidic-lite"
            ) from e

    def tokenize(self, text: str) -> list[str]:
        """Split Japanese text into word tokens, removing stop words."""
        tokens: list[str] = []
        for word in self._tagger(text):
            surface = word.surface
            if (
                surface
                and surface not in _JA_STOP_WORDS
                and len(surface) > 1
                and not _is_punctuation(surface)
            ):
                # Prefer lemma (base form) for aggregation, fall back to surface
                lemma = word.feature.lemma if hasattr(word.feature, 'lemma') else None
                tokens.append(lemma or surface)
        return tokens


def _is_punctuation(text: str) -> bool:
    """True if *text* is only punctuation characters."""
    return all(
        ord(c) < 0x3000 or (0x3000 <= ord(c) <= 0x303F and not c.isalpha())
        for c in text
    ) and not any(c.isalpha() for c in text)

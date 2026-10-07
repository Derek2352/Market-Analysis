"""Language-specific tokenizers for region-aware clustering.

Provides a ``Tokenizer`` protocol and ``get_tokenizer(region)`` factory.
Tokenizers split CJK/EN text into words for c-TF-IDF keyword extraction
during clustering.

Supported regions:
- HK, CN, TW → Traditional/Simplified Chinese (jieba)
- JP → Japanese (fugashi + UniDic)
- US, UK, AU, etc. → English (regex word split + stopwords)
"""
from src.lang.base import Tokenizer
from src.lang.en import EnglishTokenizer
from src.lang.ja import JapaneseTokenizer
from src.lang.zh import ChineseTokenizer

# English function words that leak into keyword lists from mixed-language
# posts (HK reviews switch between Cantonese and English mid-sentence).
_EN_STOP: frozenset[str] = frozenset()
try:
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS as _SK_STOP
    _EN_STOP = frozenset(_SK_STOP)
except ImportError:  # sklearn is only needed for clustering
    pass


def keyword_tokens(tokenizer: Tokenizer, text: str) -> list[str]:
    """Tokens fit for c-TF-IDF keywords: case-folded, no stopwords, no symbols.

    Region tokenizers only know their own language's stopwords, so English
    filler ("the", "to") in a Cantonese review would otherwise top a cluster's
    keyword list. ASCII tokens are lowercased so "app" and "App" count as one
    word, and tokens with no letter or digit (emoji, punctuation) are dropped.
    """
    out: list[str] = []
    for tok in tokenizer.tokenize(text):
        tok = tok.strip()
        if tok.isascii():
            tok = tok.lower()
        if not tok or tok in _EN_STOP or not any(ch.isalnum() for ch in tok):
            continue
        out.append(tok)
    return out


def get_tokenizer(region: str) -> Tokenizer:
    """Return a Tokenizer for *region*.

    Maps region codes to the appropriate tokenizer:
    - JP → JapaneseTokenizer (fugashi + UniDic)
    - HK, TW, CN → ChineseTokenizer (jieba)
    - All others → EnglishTokenizer (word split + stopwords)
    """
    if region in ("JP",):
        return JapaneseTokenizer()
    if region in ("HK", "TW", "CN"):
        return ChineseTokenizer()
    return EnglishTokenizer()

__all__ = [
    "ChineseTokenizer",
    "EnglishTokenizer",
    "JapaneseTokenizer",
    "Tokenizer",
    "get_tokenizer",
    "keyword_tokens",
]

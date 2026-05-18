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

__all__ = ["Tokenizer", "get_tokenizer", "EnglishTokenizer", "JapaneseTokenizer", "ChineseTokenizer"]

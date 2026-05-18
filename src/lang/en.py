"""English tokenizer — regex word split with stopword filtering."""
from __future__ import annotations

import re

# Common English stopwords (scikit-learn's list, trimmed for our domain).
_EN_STOPWORDS: set[str] = {
    "a", "an", "the", "and", "or", "but", "if", "because", "as", "until",
    "while", "of", "at", "by", "for", "with", "about", "against", "between",
    "into", "through", "during", "before", "after", "above", "below", "to",
    "from", "in", "out", "on", "off", "over", "under", "again", "further",
    "then", "once", "here", "there", "when", "where", "why", "how", "all",
    "both", "each", "few", "more", "most", "other", "some", "such", "no",
    "nor", "not", "only", "own", "same", "so", "than", "too", "very", "s",
    "t", "can", "will", "just", "don", "should", "now", "d", "ll", "m",
    "o", "re", "ve", "y", "ain", "aren", "couldn", "didn", "doesn",
    "hadn", "hasn", "haven", "isn", "ma", "mightn", "mustn", "needn",
    "shan", "shouldn", "wasn", "weren", "won", "wouldn",
    "this", "that", "these", "those", "am", "is", "are", "was", "were",
    "be", "been", "being", "have", "has", "had", "having", "do", "does",
    "did", "doing", "would", "could", "should", "may", "might", "must",
    "shall", "can", "will", "it", "its", "itself", "they", "them",
    "their", "theirs", "themselves", "he", "him", "his", "himself",
    "she", "her", "hers", "herself", "we", "us", "our", "ours",
    "ourselves", "you", "your", "yours", "yourself", "yourselves",
    "me", "my", "mine", "myself", "i",
}

# Matches sequences of word characters (letters, digits, apostrophes).
_WORD_RE = re.compile(r"[a-zA-Z0-9']{2,}")


class EnglishTokenizer:
    """Basic English tokenizer: whitespace + word regex + stopword removal."""

    def tokenize(self, text: str) -> list[str]:
        """Split English text into lowercase word tokens, removing stopwords."""
        words = _WORD_RE.findall(text.lower())
        return [w for w in words if w not in _EN_STOPWORDS and len(w) > 1]

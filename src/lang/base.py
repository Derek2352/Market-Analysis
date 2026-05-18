"""Tokenizer protocol and base class."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Tokenizer(Protocol):
    """A tokenizer splits raw text into a list of tokens (words/lemmas).

    Used by the clustering pipeline for c-TF-IDF keyword extraction.
    Tokenizers should handle their language's specific segmentation
    rules (e.g., CJK character-level or mecab-based for Japanese).
    """

    def tokenize(self, text: str) -> list[str]:
        """Split *text* into a list of tokens."""
        ...

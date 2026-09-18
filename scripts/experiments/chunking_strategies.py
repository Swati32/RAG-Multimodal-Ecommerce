"""Candidate chunking strategies being compared - see docs/experiments/
01-chunking-strategy.md. `sentence_aware_split` is the real production
implementation (reused, not reimplemented); `word_count_split` is the
rejected alternative, kept here only because it has no production
counterpart to import.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from ingestion.chunking import split_text as sentence_aware_split  # noqa: E402


def word_count_split(text: str, max_words: int, overlap: int) -> list[str]:
    """Rejected candidate: fixed-size word windows with overlap, no regard
    for sentence boundaries. See docs/experiments/01-chunking-strategy.md
    for why sentence_aware_split won instead."""
    words = text.split()
    if len(words) <= max_words:
        return [text.strip()] if text.strip() else []

    pieces = []
    start = 0
    while start < len(words):
        end = min(start + max_words, len(words))
        pieces.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start = end - overlap
    return pieces

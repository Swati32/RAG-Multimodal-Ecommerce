"""Candidate chunking strategies being compared - see docs/experiments/
01-chunking-strategy.md. Not the production implementation; the winner gets
merged into src/ingestion/chunking.py.
"""

import re

SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def word_count_split(text: str, max_words: int, overlap: int) -> list[str]:
    """Current production strategy (src/ingestion/chunking.py): fixed-size
    word windows with overlap, no regard for sentence boundaries."""
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


def sentence_aware_split(text: str, max_words: int) -> list[str]:
    """Candidate: pack whole sentences into a chunk up to the word budget,
    never splitting a sentence across two chunks. No overlap - each sentence
    belongs to exactly one chunk, so context isn't duplicated either."""
    sentences = [s for s in SENTENCE_BOUNDARY.split(text.strip()) if s]
    if not sentences:
        return []

    chunks = []
    current: list[str] = []
    current_words = 0
    for sentence in sentences:
        sentence_words = len(sentence.split())
        if current and current_words + sentence_words > max_words:
            chunks.append(" ".join(current))
            current, current_words = [], 0
        current.append(sentence)
        current_words += sentence_words
    if current:
        chunks.append(" ".join(current))
    return chunks

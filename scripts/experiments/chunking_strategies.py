"""Rejected chunking candidate, compared in docs/experiments/
01-chunking-strategy.md. The winning strategy is the real production
`src/ingestion/chunking.split_text` - imported directly by
run_chunking_experiment.py rather than re-exported here.
"""


def word_count_split(text: str, max_words: int, overlap: int) -> list[str]:
    """Rejected candidate: fixed-size word windows with overlap, no regard
    for sentence boundaries. See docs/experiments/01-chunking-strategy.md
    for why sentence-aware splitting won instead."""
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

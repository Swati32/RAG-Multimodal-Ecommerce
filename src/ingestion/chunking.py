import re

from .models import Chunk, Product, Review
from .summarization import ClaudeClient, summarize_review_if_long

# Word count is used as a token-count proxy to avoid a tokenizer dependency;
# it's a deliberate approximation, not exact token counting.
MAX_CHUNK_WORDS = 300

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def split_text(text: str, max_words: int = MAX_CHUNK_WORDS) -> list[str]:
    """Packs whole sentences into a chunk up to the word budget - never
    splits a sentence across two chunks, so no overlap is needed either.

    Chosen over fixed-size word windows with overlap after a direct
    comparison: see docs/experiments/01-chunking-strategy.md. A single
    sentence longer than max_words becomes its own oversized chunk - not
    worth the extra complexity for this dataset.
    """
    sentences = [s for s in _SENTENCE_BOUNDARY.split(text.strip()) if s]
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


def build_tag_summary(product: Product) -> str:
    if product.brand:
        return f"{product.title} by {product.brand}, category: {product.category}"
    return f"{product.title}, category: {product.category}"


def chunk_product(product: Product) -> list[Chunk]:
    chunks = [
        Chunk(
            chunk_id=f"{product.product_id}#desc{i}",
            product_id=product.product_id,
            chunk_type="description",
            text=piece,
        )
        for i, piece in enumerate(split_text(product.description))
    ]
    chunks.append(
        Chunk(
            chunk_id=f"{product.product_id}#tags",
            product_id=product.product_id,
            chunk_type="tag_summary",
            text=build_tag_summary(product),
        )
    )
    return chunks


def chunk_review(review: Review, claude_client: ClaudeClient | None = None) -> list[Chunk]:
    text = review.text
    if claude_client is not None:
        # Design doc 02, "Agents vs Direct LLM Calls": summarize a long review
        # with a direct Claude call before chunking, rather than splitting it
        # blind and losing the review's overall point across pieces.
        text = summarize_review_if_long(text, claude_client)

    return [
        Chunk(
            chunk_id=f"{review.product_id}#review-{review.review_id}-{i}",
            product_id=review.product_id,
            chunk_type="review",
            text=piece,
            review_id=review.review_id,
        )
        for i, piece in enumerate(split_text(text))
    ]

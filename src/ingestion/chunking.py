from .models import Chunk, Product, Review
from .summarization import ClaudeClient, summarize_review_if_long

# Word count is used as a token-count proxy to avoid a tokenizer dependency;
# it's a deliberate approximation, not exact token counting.
MAX_CHUNK_WORDS = 300
CHUNK_OVERLAP_WORDS = 50


def split_text(text: str, max_words: int = MAX_CHUNK_WORDS, overlap: int = CHUNK_OVERLAP_WORDS) -> list[str]:
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

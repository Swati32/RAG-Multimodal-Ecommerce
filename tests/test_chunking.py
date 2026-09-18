from ingestion.chunking import build_tag_summary, chunk_product, chunk_review, split_text
from ingestion.models import Product, Review


def make_product(description: str = "A great product.") -> Product:
    return Product(
        product_id="P1",
        title="Wireless Headphones",
        category="Electronics/Audio/Headphones",
        category_path=["Electronics", "Audio", "Headphones"],
        price=49.99,
        avg_rating=4.5,
        review_count=120,
        description=description,
        brand="Acme",
    )


def test_split_text_keeps_short_text_whole():
    assert split_text("a short sentence") == ["a short sentence"]


def test_split_text_packs_whole_sentences_without_breaking_them():
    def sentence(n: int) -> str:
        return f"Sentence{n} " + " ".join(["word"] * 99) + "."  # 100 words

    text = " ".join(sentence(i) for i in range(4))  # 4 x 100 = 400 words

    pieces = split_text(text, max_words=300)

    assert len(pieces) == 2
    assert pieces[0].startswith("Sentence0")
    assert "Sentence3" not in pieces[0]
    assert pieces[1].startswith("Sentence3")
    assert all(p.rstrip().endswith(".") for p in pieces)  # never cut mid-sentence


def test_split_text_keeps_an_oversized_single_sentence_whole():
    text = " ".join(["word"] * 400) + "."  # one sentence, no internal punctuation

    pieces = split_text(text, max_words=300)

    assert len(pieces) == 1
    assert len(pieces[0].split()) == 400


def test_split_text_ignores_blank_input():
    assert split_text("   ") == []


def test_build_tag_summary_includes_brand_and_category():
    summary = build_tag_summary(make_product())
    assert "Acme" in summary
    assert "Electronics/Audio/Headphones" in summary


def test_chunk_product_produces_description_and_tag_chunks():
    chunks = chunk_product(make_product())

    types = [c.chunk_type for c in chunks]
    assert types == ["description", "tag_summary"]
    assert all(c.product_id == "P1" for c in chunks)
    assert chunks[0].chunk_id == "P1#desc0"
    assert chunks[1].chunk_id == "P1#tags"


def test_chunk_review_carries_review_id():
    review = Review(review_id="R1", product_id="P1", rating=5, text="Loved it.", timestamp=0)

    chunks = chunk_review(review)

    assert len(chunks) == 1
    assert chunks[0].review_id == "R1"
    assert chunks[0].chunk_id == "P1#review-R1-0"


def test_chunk_review_summarizes_long_text_via_claude_client_before_splitting():
    class StubClaudeClient:
        def summarize(self, prompt: str) -> str:
            return "Short summary."

    long_text = " ".join(["word"] * 400)
    review = Review(review_id="R1", product_id="P1", rating=3, text=long_text, timestamp=0)

    chunks = chunk_review(review, claude_client=StubClaudeClient())

    assert len(chunks) == 1
    assert chunks[0].text == "Short summary."

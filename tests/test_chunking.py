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


def test_split_text_splits_long_text_with_overlap():
    words = [f"word{i}" for i in range(700)]
    text = " ".join(words)

    pieces = split_text(text, max_words=300, overlap=50)

    assert len(pieces) == 3
    assert pieces[0].split()[0] == "word0"
    assert pieces[1].split()[0] == "word250"  # 300 - 50 overlap
    assert pieces[-1].split()[-1] == "word699"


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

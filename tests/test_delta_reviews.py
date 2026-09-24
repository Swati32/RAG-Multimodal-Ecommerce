from ingestion.delta_reviews import select_new_reviews
from ingestion.models import Review


def _review(review_id: str, product_id: str, timestamp: int = 1700000000) -> Review:
    return Review(review_id=review_id, product_id=product_id, rating=5, text="Nice.", timestamp=timestamp)


def test_selects_only_new_reviews_for_known_products():
    reviews = [_review("R1", "P1"), _review("R2", "P1"), _review("R3", "PUNKNOWN")]

    selected = select_new_reviews(
        reviews, known_product_ids={"P1"}, already_loaded_review_ids_by_product={"P1": {"R1"}}, max_total=10, max_per_product=10
    )

    assert [r.review_id for r in selected] == ["R2"]


def test_respects_max_per_product():
    reviews = [_review("R1", "P1"), _review("R2", "P1"), _review("R3", "P1")]

    selected = select_new_reviews(reviews, known_product_ids={"P1"}, already_loaded_review_ids_by_product={}, max_total=10, max_per_product=2)

    assert [r.review_id for r in selected] == ["R1", "R2"]


def test_respects_max_total_across_products():
    reviews = [_review("R1", "P1"), _review("R2", "P2"), _review("R3", "P3")]

    selected = select_new_reviews(reviews, known_product_ids={"P1", "P2", "P3"}, already_loaded_review_ids_by_product={}, max_total=2, max_per_product=10)

    assert [r.review_id for r in selected] == ["R1", "R2"]


def test_preserves_stream_order():
    reviews = [_review("R3", "P1", timestamp=3), _review("R1", "P1", timestamp=1), _review("R2", "P1", timestamp=2)]

    selected = select_new_reviews(reviews, known_product_ids={"P1"}, already_loaded_review_ids_by_product={}, max_total=10, max_per_product=10)

    assert [r.review_id for r in selected] == ["R3", "R1", "R2"]


def test_returns_empty_when_nothing_new():
    reviews = [_review("R1", "P1")]
    selected = select_new_reviews(reviews, known_product_ids={"P1"}, already_loaded_review_ids_by_product={"P1": {"R1"}}, max_total=10, max_per_product=10)
    assert selected == []

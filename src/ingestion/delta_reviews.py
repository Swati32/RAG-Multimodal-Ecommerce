"""Selects which streamed reviews are genuinely new for the daily refresh
cadence - not already loaded, for a product already in the catalog, up to
a per-run cap. See docs/designs/01-ingestion-pipeline.md, "Simulating
refresh cadence".
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .models import Review


def select_new_reviews(
    reviews: Iterable[Review],
    known_product_ids: set[str],
    already_loaded_review_ids_by_product: dict[str, set[str]],
    max_total: int,
    max_per_product: int,
) -> list[Review]:
    selected: list[Review] = []
    count_by_product: dict[str, int] = defaultdict(int)
    for review in reviews:
        if len(selected) >= max_total:
            break
        if review.product_id not in known_product_ids:
            continue
        if review.review_id in already_loaded_review_ids_by_product.get(review.product_id, ()):
            continue
        if count_by_product[review.product_id] >= max_per_product:
            continue
        selected.append(review)
        count_by_product[review.product_id] += 1
    return selected

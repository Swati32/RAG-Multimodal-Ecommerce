from __future__ import annotations

from collections import defaultdict
from itertools import combinations

from .models import Product, Review


def category_edges(product: Product) -> list[tuple[str, str]]:
    product_node = f"product#{product.product_id}"
    category_node = f"category#{product.category}"
    return [
        (product_node, f"BELONGS_TO#{category_node}"),
        (category_node, f"HAS_PRODUCT#{product_node}"),
    ]


def brand_edges(product: Product) -> list[tuple[str, str]]:
    if not product.brand:
        return []
    product_node = f"product#{product.product_id}"
    brand_node = f"brand#{product.brand}"
    return [
        (product_node, f"HAS_BRAND#{brand_node}"),
        (brand_node, f"HAS_PRODUCT#{product_node}"),
    ]


def _reviewer_id(review: Review) -> str:
    # review_id is "{timestamp}#{user_id}" (see dataset_source.parse_review) -
    # the only place the source dataset's reviewer id survives ingestion.
    return review.review_id.split("#", 1)[1]


def co_reviewed_edges(reviews: list[Review]) -> list[tuple[str, str]]:
    """Products reviewed by the same person - a real signal standing in for
    the dataset's unpopulated `bought_together` field, see docs/designs/
    01-ingestion-pipeline.md#graph-storage-dynamodb-not-neptune."""
    products_by_reviewer: dict[str, set[str]] = defaultdict(set)
    for review in reviews:
        products_by_reviewer[_reviewer_id(review)].add(review.product_id)

    edges = []
    for product_ids in products_by_reviewer.values():
        for a, b in combinations(sorted(product_ids), 2):
            edges.append((f"product#{a}", f"CO_REVIEWED_WITH#product#{b}"))
            edges.append((f"product#{b}", f"CO_REVIEWED_WITH#product#{a}"))
    return edges

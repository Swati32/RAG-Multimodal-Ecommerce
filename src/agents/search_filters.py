"""Structured filter building for OpenSearch queries - shared by
SearchAgent (chunks index) and ImageAgent (product_images index), which
filter on the same product-level fields. See docs/designs/02-retrieval-
agents.md, "OpenSearch metadata & filtering".
"""

from __future__ import annotations


def build_filters(
    category: str | None,
    brand: str | None,
    min_price: float | None,
    max_price: float | None,
    min_rating: float | None,
    in_stock: bool | None,
) -> list[dict]:
    filters = []
    if category:
        filters.append({"term": {"category": category}})
    if brand:
        filters.append({"term": {"brand": brand}})
    if min_price is not None or max_price is not None:
        price_range = {}
        if min_price is not None:
            price_range["gte"] = min_price
        if max_price is not None:
            price_range["lte"] = max_price
        filters.append({"range": {"price": price_range}})
    if min_rating is not None:
        filters.append({"range": {"avg_rating": {"gte": min_rating}}})
    if in_stock is not None:
        filters.append({"term": {"in_stock": in_stock}})
    return filters


def with_filters(clause: dict, filters: list[dict]) -> dict:
    return {"bool": {"must": [clause], "filter": filters}} if filters else clause

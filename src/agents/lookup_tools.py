"""LookupAgent's tool implementations - direct DynamoDB retrieval by exact
id, no search/ranking. See docs/designs/02-retrieval-agents.md, LookupAgent.
Called from the Claude tool-use loop in lookup_agent_runtime.py.

Reviews require both product_id and review_id (the Reviews table's key is
the composite (product_id, review_id) - review_id alone was never a lookup
key, and every review id this system surfaces already carries its
product_id alongside it via chunk citations).
"""

from __future__ import annotations

from dataclasses import asdict

from ingestion.dynamo_reader import product_from_item, review_from_item


def get_product_response(table, product_id: str | None) -> dict:
    if not product_id:
        return {"error": "product_id is required"}
    item = table.get_item(Key={"product_id": product_id}).get("Item")
    if item is None:
        return {"error": f"No product found with product_id={product_id}"}
    return asdict(product_from_item(item))


def get_review_response(table, product_id: str | None, review_id: str | None) -> dict:
    if not product_id or not review_id:
        return {"error": "both product_id and review_id are required"}
    item = table.get_item(Key={"product_id": product_id, "review_id": review_id}).get("Item")
    if item is None:
        return {"error": f"No review found with product_id={product_id}, review_id={review_id}"}
    return asdict(review_from_item(item))

"""ImageAgent's tool implementation - k-NN search over the `product_images`
index, with the same structured filters as SearchAgent. See docs/designs/
02-retrieval-agents.md, ImageAgent, and docs/designs/01-ingestion-
pipeline.md ("Image embeddings") for why this is Cohere Embed v4, not
Titan Multimodal, and 1024-dim, not Cohere's 1536 default.

Pure k-NN, no hybrid/text component - there's no text signal for a photo
query the way SearchAgent has review/description text to match against.
"""

from __future__ import annotations

from agents.search_filters import build_filters, with_filters

DEFAULT_K = 5


def search_similar_images(
    client,
    index_name: str,
    embedding: list[float],
    *,
    category: str | None = None,
    brand: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    min_rating: float | None = None,
    in_stock: bool | None = None,
    k: int = DEFAULT_K,
) -> list[dict]:
    filters = build_filters(category, brand, min_price, max_price, min_rating, in_stock)
    knn_clause = with_filters({"knn": {"embedding": {"vector": embedding, "k": k}}}, filters)

    response = client.search(index=index_name, body={"size": k, "query": knn_clause})
    return [
        {
            "image_id": hit["_source"]["image_id"],
            "product_id": hit["_source"]["product_id"],
            "image_url": hit["_source"]["image_url"],
            "category": hit["_source"]["category"],
            "brand": hit["_source"]["brand"],
            "price": hit["_source"]["price"],
            "avg_rating": hit["_source"]["avg_rating"],
        }
        for hit in response["hits"]["hits"]
    ]

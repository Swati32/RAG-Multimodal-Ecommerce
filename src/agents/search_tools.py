"""SearchAgent's tool implementation - OpenSearch hybrid (BM25 + k-NN)
search over the `chunks` index, with structured filters. See docs/designs/
02-retrieval-agents.md, SearchAgent, and docs/experiments/
05-searchagent-fusion-method.md for why the hybrid combination is a native
OpenSearch normalization pipeline, not a naive bool(knn, match) sum.
"""

from __future__ import annotations

from agents.search_filters import build_filters, with_filters

PIPELINE_NAME = "searchagent-hybrid-pipeline"
DEFAULT_K = 5


def ensure_search_pipeline(client) -> None:
    """Idempotent - safe to call on every cold start, mirrors the
    create-index-if-missing pattern in load_opensearch.py."""
    client.transport.perform_request(
        "PUT",
        f"/_search/pipeline/{PIPELINE_NAME}",
        body={
            "description": "min-max normalize + equal-weight combine BM25 and kNN - see docs/experiments/05-searchagent-fusion-method.md",
            "phase_results_processors": [
                {
                    "normalization-processor": {
                        "normalization": {"technique": "min_max"},
                        "combination": {"technique": "arithmetic_mean", "parameters": {"weights": [0.5, 0.5]}},
                    }
                }
            ],
        },
    )


def search_products(
    client,
    index_name: str,
    query_text: str,
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
    match_clause = with_filters({"match": {"text": query_text}}, filters)
    knn_clause = with_filters({"knn": {"embedding": {"vector": embedding, "k": k}}}, filters)

    response = client.search(
        index=index_name,
        params={"search_pipeline": PIPELINE_NAME},
        body={"size": k, "query": {"hybrid": {"queries": [match_clause, knn_clause]}}},
    )
    return [
        {
            "chunk_id": hit["_source"]["chunk_id"],
            "product_id": hit["_source"]["product_id"],
            "category": hit["_source"]["category"],
            "brand": hit["_source"]["brand"],
            "price": hit["_source"]["price"],
            "avg_rating": hit["_source"]["avg_rating"],
            "chunk_type": hit["_source"]["chunk_type"],
            "text": hit["_source"]["text"],
        }
        for hit in response["hits"]["hits"]
    ]

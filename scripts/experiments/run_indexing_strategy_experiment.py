"""Validates the hybrid combination method question flagged (not yet
decided) in docs/designs/02-retrieval-agents.md, "Indexing strategy" -
see docs/experiments/04-indexing-strategy.md.

Builds one sentence-aware chunk index (the confirmed strategy from
Experiment 03) over a real review pool, then compares four ways of
turning a query into ranked results against the SAME index and queries:
k-NN only, BM25 only, a naive bool(knn, match) combination (the default
OpenSearch scoring design doc 02 describes), and client-side reciprocal
rank fusion (RRF) of the k-NN-only and BM25-only ranked lists.

Standalone local script, like the other scripts/experiments/ - not a
Glue job, and not part of SearchAgent (not built yet, workflow 02).
"""

import random
import sys
import time
from pathlib import Path

import boto3
from opensearchpy import OpenSearch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from ingestion.chunking import split_text  # noqa: E402
from retrieval_eval_utils import (  # noqa: E402
    REGION,
    build_index,
    fetch_review_pool,
    opensearch_client,
    opensearch_endpoint,
    precision_and_hit_rate_at_k,
    synthesize_queries,
)

POOL_SIZE = 300
N_QUERIES = 30
MIN_WORDS = 150
K = 5  # final precision@k / hit_rate@k, comparable across all four methods
CANDIDATE_SIZE = 20  # per-method ranked-list depth fed into RRF fusion
RRF_C = 60  # standard RRF damping constant
SEED = 42

INDEX_NAME = "eval-chunks-indexing-strategy"


def knn_search(client: OpenSearch, index_name: str, vector: list[float], size: int) -> list[dict]:
    response = client.search(
        index=index_name, body={"size": size, "query": {"knn": {"embedding": {"vector": vector, "k": size}}}}
    )
    return response["hits"]["hits"]


def bm25_search(client: OpenSearch, index_name: str, text: str, size: int) -> list[dict]:
    response = client.search(index=index_name, body={"size": size, "query": {"match": {"text": text}}})
    return response["hits"]["hits"]


def naive_hybrid_search(client: OpenSearch, index_name: str, vector: list[float], text: str, k: int) -> list[str]:
    """The naive combination design doc 02 describes: knn + match in one
    bool/should, OpenSearch's default (summed) scoring - not RRF, not a
    normalization pipeline."""
    try:
        response = client.search(
            index=index_name,
            body={
                "size": k,
                "query": {
                    "bool": {
                        "should": [
                            {"knn": {"embedding": {"vector": vector, "k": k}}},
                            {"match": {"text": text}},
                        ]
                    }
                },
            },
        )
        return [hit["_source"]["review_id"] for hit in response["hits"]["hits"]]
    except Exception as exc:  # noqa: BLE001 - documenting real OpenSearch behavior, not swallowing silently
        print(f"  bool(knn, match) rejected by OpenSearch ({exc}); falling back to manual raw-score sum.")
        knn_hits = knn_search(client, index_name, vector, CANDIDATE_SIZE)
        bm25_hits = bm25_search(client, index_name, text, CANDIDATE_SIZE)
        combined: dict = {}
        for hit in knn_hits + bm25_hits:
            entry = combined.setdefault(hit["_id"], {"score": 0.0, "review_id": hit["_source"]["review_id"]})
            entry["score"] += hit["_score"]
        ranked = sorted(combined.values(), key=lambda e: e["score"], reverse=True)[:k]
        return [e["review_id"] for e in ranked]


def rrf_fuse(ranked_lists: list[list[str]], k: int, c: int = RRF_C) -> list[str]:
    scores: dict = {}
    for ranked in ranked_lists:
        for rank, review_id in enumerate(ranked, start=1):
            scores[review_id] = scores.get(review_id, 0.0) + 1.0 / (c + rank)
    return [review_id for review_id, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:k]]


def main() -> None:
    print(f"Streaming a pool of {POOL_SIZE} long reviews (>= {MIN_WORDS} words)...")
    pool = fetch_review_pool(POOL_SIZE, MIN_WORDS)

    rng = random.Random(SEED)
    targets = rng.sample(pool, N_QUERIES)

    runtime = boto3.client("bedrock-runtime", region_name=REGION)
    print(f"Synthesizing and embedding {N_QUERIES} queries...")
    queries = synthesize_queries(targets, runtime)

    endpoint = opensearch_endpoint()
    client = opensearch_client(endpoint)

    print("\nBuilding sentence-aware index (the confirmed chunking strategy)...")
    build_index(client, INDEX_NAME, runtime, pool, lambda text: split_text(text, max_words=300))
    time.sleep(2)

    methods = {}
    for query in queries:
        knn_hits = knn_search(client, INDEX_NAME, query["embedding"], CANDIDATE_SIZE)
        bm25_hits = bm25_search(client, INDEX_NAME, query["text"], CANDIDATE_SIZE)
        knn_review_ids = [h["_source"]["review_id"] for h in knn_hits]
        bm25_review_ids = [h["_source"]["review_id"] for h in bm25_hits]

        methods.setdefault("knn_only", []).append(knn_review_ids[:K])
        methods.setdefault("bm25_only", []).append(bm25_review_ids[:K])
        methods.setdefault("naive_hybrid", []).append(
            naive_hybrid_search(client, INDEX_NAME, query["embedding"], query["text"], K)
        )
        methods.setdefault("rrf", []).append(rrf_fuse([knn_review_ids, bm25_review_ids], K))

    target_review_ids = [q["review_id"] for q in queries]
    print(f"\nResults (n={N_QUERIES} queries, k={K}, corpus={POOL_SIZE} reviews, sentence-aware index):")
    for name, retrieved in methods.items():
        precision, hit_rate = precision_and_hit_rate_at_k(retrieved, target_review_ids, K)
        print(f"{name}: precision@{K}={precision:.3f} hit_rate@{K}={hit_rate:.3f}")

    print("\nCleaning up eval index...")
    client.indices.delete(index=INDEX_NAME)


if __name__ == "__main__":
    main()

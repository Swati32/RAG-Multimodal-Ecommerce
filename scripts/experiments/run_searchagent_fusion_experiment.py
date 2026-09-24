"""Closes the follow-up Experiment 04 left open ("tuned once SearchAgent
exists") - docs/experiments/05-searchagent-fusion-method.md. Experiment 04
proved naive bool(knn, match) degrades to BM25-only and unweighted RRF
underperforms both single methods (it drags in knn's weaker ranking with no
way to favor the stronger one). This tests real fixes: weighted RRF at a
few ratios, and OpenSearch's native `hybrid` query with a normalization
search pipeline (opensearch-neural-search - confirmed installed on this
domain via `_cat/plugins`, not assumed).

Same corpus/query methodology as Experiment 04 - see that doc and
retrieval_eval_utils.py for the shared plumbing and why synthetic queries
are used.
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
K = 5
CANDIDATE_SIZE = 20
RRF_C = 60
SEED = 42

INDEX_NAME = "eval-chunks-searchagent-fusion"
PIPELINE_NAME = "eval-hybrid-normalization-pipeline"

# (bm25_weight, knn_weight) - bm25 individually outperformed knn in
# Experiment 04 (0.213 vs 0.207 precision@5), so weighting toward it is the
# natural first thing to try, not an arbitrary sweep.
RRF_WEIGHT_VARIANTS = {
    "rrf_unweighted": (1.0, 1.0),
    "rrf_favor_bm25_2to1": (2.0, 1.0),
    "rrf_favor_bm25_3to1": (3.0, 1.0),
}

# (bm25_weight, knn_weight) for OpenSearch's native min-max + weighted-mean
# hybrid combination - must sum to 1.0 for arithmetic_mean.
NATIVE_HYBRID_WEIGHT_VARIANTS = {
    "native_hybrid_equal": (0.5, 0.5),
    "native_hybrid_favor_bm25": (0.7, 0.3),
}


def knn_search(client: OpenSearch, index_name: str, vector: list[float], size: int) -> list[dict]:
    response = client.search(
        index=index_name, body={"size": size, "query": {"knn": {"embedding": {"vector": vector, "k": size}}}}
    )
    return response["hits"]["hits"]


def bm25_search(client: OpenSearch, index_name: str, text: str, size: int) -> list[dict]:
    response = client.search(index=index_name, body={"size": size, "query": {"match": {"text": text}}})
    return response["hits"]["hits"]


def rrf_fuse(ranked_lists_with_weights: list[tuple[list[str], float]], k: int, c: int = RRF_C) -> list[str]:
    scores: dict = {}
    for ranked, weight in ranked_lists_with_weights:
        for rank, review_id in enumerate(ranked, start=1):
            scores[review_id] = scores.get(review_id, 0.0) + weight / (c + rank)
    return [review_id for review_id, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:k]]


def setup_normalization_pipeline(client: OpenSearch, bm25_weight: float, knn_weight: float) -> None:
    client.transport.perform_request(
        "PUT",
        f"/_search/pipeline/{PIPELINE_NAME}",
        body={
            "description": "min-max normalize + weighted-mean combine BM25 and kNN",
            "phase_results_processors": [
                {
                    "normalization-processor": {
                        "normalization": {"technique": "min_max"},
                        "combination": {
                            "technique": "arithmetic_mean",
                            "parameters": {"weights": [bm25_weight, knn_weight]},
                        },
                    }
                }
            ],
        },
    )


def native_hybrid_search(client: OpenSearch, index_name: str, vector: list[float], text: str, k: int) -> list[str]:
    response = client.search(
        index=index_name,
        params={"search_pipeline": PIPELINE_NAME},
        body={
            "size": k,
            "query": {"hybrid": {"queries": [{"match": {"text": text}}, {"knn": {"embedding": {"vector": vector, "k": k}}}]}},
        },
    )
    return [hit["_source"]["review_id"] for hit in response["hits"]["hits"]]


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

    print("\nBuilding sentence-aware index...")
    build_index(client, INDEX_NAME, runtime, pool, lambda text: split_text(text, max_words=300))
    time.sleep(2)

    target_review_ids = [q["review_id"] for q in queries]
    results = {}

    # Collect raw per-query candidate lists once, reused across RRF variants.
    knn_lists, bm25_lists = [], []
    for query in queries:
        knn_lists.append([h["_source"]["review_id"] for h in knn_search(client, INDEX_NAME, query["embedding"], CANDIDATE_SIZE)])
        bm25_lists.append([h["_source"]["review_id"] for h in bm25_search(client, INDEX_NAME, query["text"], CANDIDATE_SIZE)])

    for name, (bm25_w, knn_w) in RRF_WEIGHT_VARIANTS.items():
        retrieved = [
            rrf_fuse([(bm25_list, bm25_w), (knn_list, knn_w)], K)
            for bm25_list, knn_list in zip(bm25_lists, knn_lists)
        ]
        results[name] = precision_and_hit_rate_at_k(retrieved, target_review_ids, K)

    for name, (bm25_w, knn_w) in NATIVE_HYBRID_WEIGHT_VARIANTS.items():
        setup_normalization_pipeline(client, bm25_w, knn_w)
        retrieved = [native_hybrid_search(client, INDEX_NAME, q["embedding"], q["text"], K) for q in queries]
        results[name] = precision_and_hit_rate_at_k(retrieved, target_review_ids, K)

    print(f"\nResults (n={N_QUERIES} queries, k={K}, corpus={POOL_SIZE} reviews):")
    print(f"(reference from Experiment 04: bm25_only=0.213/0.967, knn_only=0.207/0.933)")
    for name, (precision, hit_rate) in results.items():
        print(f"{name}: precision@{K}={precision:.3f} hit_rate@{K}={hit_rate:.3f}")

    print("\nCleaning up...")
    client.indices.delete(index=INDEX_NAME)
    client.transport.perform_request("DELETE", f"/_search/pipeline/{PIPELINE_NAME}")


if __name__ == "__main__":
    main()

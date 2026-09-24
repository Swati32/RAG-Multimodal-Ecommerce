"""Validates the chunking-strategy decision from docs/experiments/
01-chunking-strategy.md against real retrieval, not just structural
proxy metrics - see docs/experiments/03-chunking-retrieval-validation.md.

Chunks the same pool of real long reviews under both strategies, embeds
and indexes each into its own throwaway OpenSearch index, then measures
whether synthetic queries retrieve the correct source review.

Standalone local script, like the other scripts/experiments/ - not a Glue
job. It depends on awsglue-free modules only (unlike the production Glue
scripts) so it can run directly against real AWS from a dev machine.
"""

import random
import sys
import time
from pathlib import Path

import boto3
from opensearchpy import OpenSearch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from ingestion.chunking import split_text  # noqa: E402
from chunking_strategies import word_count_split  # noqa: E402
from retrieval_eval_utils import (  # noqa: E402
    REGION,
    build_index,
    fetch_review_pool,
    opensearch_client,
    opensearch_endpoint,
    precision_and_hit_rate_at_k,
    synthesize_queries,
)

POOL_SIZE = 300  # long reviews indexed as retrieval corpus (mostly distractors)
N_QUERIES = 30  # of the pool, how many get a synthesized query + are scored
MIN_WORDS = 150  # same "long review" threshold as Experiment 01
K = 5  # precision@k
SEED = 42

INDEX_SENTENCE_AWARE = "eval-chunks-sentence-aware"
INDEX_WORD_COUNT = "eval-chunks-word-count"

STRATEGIES = {
    "sentence_aware": lambda text: split_text(text, max_words=300),
    "word_count": lambda text: word_count_split(text, max_words=300, overlap=50),
}


def knn_search_review_ids(client: OpenSearch, index_name: str, vector: list[float], k: int) -> list[str]:
    response = client.search(
        index=index_name, body={"size": k, "query": {"knn": {"embedding": {"vector": vector, "k": k}}}}
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

    results = {}
    for name, chunk_fn in STRATEGIES.items():
        index_name = INDEX_SENTENCE_AWARE if name == "sentence_aware" else INDEX_WORD_COUNT
        print(f"\nBuilding {name} index...")
        build_index(client, index_name, runtime, pool, chunk_fn)
        time.sleep(2)  # let the index refresh before querying

        retrieved = [knn_search_review_ids(client, index_name, q["embedding"], K) for q in queries]
        precision, hit_rate = precision_and_hit_rate_at_k(retrieved, [q["review_id"] for q in queries], K)
        results[name] = (precision, hit_rate)

    print(f"\nResults (n={N_QUERIES} queries, k={K}, corpus={POOL_SIZE} reviews):")
    for name, (precision, hit_rate) in results.items():
        print(f"{name}: precision@{K}={precision:.3f} hit_rate@{K}={hit_rate:.3f}")

    print("\nCleaning up eval indices...")
    for index_name in (INDEX_SENTENCE_AWARE, INDEX_WORD_COUNT):
        client.indices.delete(index=index_name)


if __name__ == "__main__":
    main()

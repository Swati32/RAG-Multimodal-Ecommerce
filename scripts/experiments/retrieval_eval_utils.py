"""Shared plumbing for the retrieval-validation experiments (chunking,
indexing strategy) - real Titan embeddings, real OpenSearch, real synthetic
queries generated from real reviews via Claude. Not used by production code.
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection, helpers

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from dataset_source import parse_review, stream_reviews  # noqa: E402
from ingestion.opensearch_documents import EMBEDDING_DIM  # noqa: E402
from ingestion.summarization import BedrockClaudeClient  # noqa: E402

REGION = "us-east-2"
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
QUERY_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

QUERY_PROMPT = (
    "Here is a real product review. Write ONE short, natural shopping "
    "question (5-15 words) that a person would type into a search box if "
    "this review would help answer it. Do not quote the review directly. "
    "Respond with ONLY the question text, no quotes, no other text.\n\n"
    "Review:\n{review_text}"
)

EVAL_INDEX_MAPPING = {
    "settings": {"index": {"knn": True}},
    "mappings": {
        "properties": {
            "chunk_id": {"type": "keyword"},
            "review_id": {"type": "keyword"},
            "text": {"type": "text"},
            "embedding": {
                "type": "knn_vector",
                "dimension": EMBEDDING_DIM,
                "method": {"engine": "lucene", "name": "hnsw", "space_type": "cosinesimil"},
            },
        }
    },
}


def opensearch_endpoint() -> str:
    client = boto3.client("opensearch", region_name=REGION)
    domain_name = client.list_domain_names()["DomainNames"][0]["DomainName"]
    return client.describe_domain(DomainName=domain_name)["DomainStatus"]["Endpoint"]


def opensearch_client(endpoint: str) -> OpenSearch:
    credentials = boto3.Session().get_credentials()
    auth = AWSV4SignerAuth(credentials, REGION, "es")
    return OpenSearch(
        hosts=[{"host": endpoint, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
    )


def fetch_review_pool(size: int, min_words: int) -> list[dict]:
    pool = []
    for raw in stream_reviews():
        review_id, product_id, rating, text, timestamp = parse_review(raw)
        if len(text.split()) >= min_words:
            pool.append({"review_id": review_id, "product_id": product_id, "text": text})
        if len(pool) >= size:
            break
    return pool


def embed_texts(runtime, texts: list[str]) -> list[list[float]]:
    def embed_one(text: str) -> list[float]:
        body = json.dumps({"inputText": text})
        response = runtime.invoke_model(modelId=EMBEDDING_MODEL_ID, body=body)
        return json.loads(response["body"].read())["embedding"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(embed_one, texts))


def synthesize_queries(targets: list[dict], runtime) -> list[dict]:
    """One synthetic query per target review, embedded and ready to search with."""
    query_client = BedrockClaudeClient(QUERY_MODEL_ID, region=REGION, max_tokens=60)
    queries = [
        {"review_id": t["review_id"], "text": query_client.summarize(QUERY_PROMPT.format(review_text=t["text"]))}
        for t in targets
    ]
    embeddings = embed_texts(runtime, [q["text"] for q in queries])
    for query, embedding in zip(queries, embeddings):
        query["embedding"] = embedding
    return queries


def build_index(client: OpenSearch, index_name: str, runtime, reviews: list[dict], chunk_fn) -> None:
    """(Re)builds a throwaway eval index: chunks every review with chunk_fn,
    embeds every chunk, bulk-indexes. chunk_fn: str -> list[str]."""
    if client.indices.exists(index=index_name):
        client.indices.delete(index=index_name)
    client.indices.create(index=index_name, body=EVAL_INDEX_MAPPING)

    chunk_records = []
    for review in reviews:
        for i, piece in enumerate(chunk_fn(review["text"])):
            chunk_records.append(
                {"chunk_id": f"{review['review_id']}#{i}", "review_id": review["review_id"], "text": piece}
            )

    embeddings = embed_texts(runtime, [r["text"] for r in chunk_records])
    for record, embedding in zip(chunk_records, embeddings):
        record["embedding"] = embedding

    actions = ({"_index": index_name, "_id": r["chunk_id"], "_source": r} for r in chunk_records)
    success, errors = helpers.bulk(client, actions, chunk_size=100, max_retries=5, initial_backoff=2)
    print(f"{index_name}: indexed {success} chunks from {len(reviews)} reviews, {len(errors)} errors.")


def precision_and_hit_rate_at_k(retrieved_review_ids_per_query: list[list[str]], target_review_ids: list[str], k: int) -> tuple:
    precisions, hits = [], []
    for retrieved, target_id in zip(retrieved_review_ids_per_query, target_review_ids):
        relevant = sum(1 for rid in retrieved[:k] if rid == target_id)
        precisions.append(relevant / k)
        hits.append(1.0 if relevant > 0 else 0.0)
    return sum(precisions) / len(precisions), sum(hits) / len(hits)

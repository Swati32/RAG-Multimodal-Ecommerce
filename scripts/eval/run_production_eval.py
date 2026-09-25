"""Production evaluation harness - runs a held-out set of synthetic
queries through the *real deployed system* (query API -> router ->
specialists -> generator -> verifier), scores retrieval + groundedness,
and publishes results as CloudWatch custom metrics under RAGEcommerce/Eval.
See docs/designs/05-observability-cost.md, "Evaluation framework", and
docs/experiments/06-production-eval.md for the full method and results.

Run manually after any change to prompts, models, or retrieval config -
not on a schedule (each run costs real Bedrock tokens against real
deployed infrastructure, see design doc 05's "When it runs").

Usage: .venv/bin/python scripts/eval/run_production_eval.py [--n 20] [--api-url URL]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import boto3
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_utils import build_judge_prompt, build_query_prompt, hit_at_k, parse_json_response  # noqa: E402
from ingestion.dynamo_reader import product_from_item, scan_all_items  # noqa: E402
from ingestion.summarization import BedrockClaudeClient  # noqa: E402

REGION = "us-east-2"
QUERY_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
# A different, stronger model than the query-synthesis call, per design
# doc 05's "ideally a different/stronger model... to avoid self-grading
# bias" - this is the same model the production generator uses (Sonnet
# 4.5), which is a real, flagged limitation (not a fully independent
# judge) rather than a different model family, since no stronger model is
# available in this project's already-used set. See the experiment doc.
JUDGE_MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

# Published on-demand Bedrock pricing (per 1K tokens, us-east-2, at the
# time this was written) - used only for a rough CostPerQueryUsd ESTIMATE,
# since the router doesn't currently report real token usage. Flagged as
# an estimate, not a metered value, in the experiment doc.
HAIKU_PRICE_PER_1K_INPUT = 0.001
HAIKU_PRICE_PER_1K_OUTPUT = 0.005
SONNET_PRICE_PER_1K_INPUT = 0.003
SONNET_PRICE_PER_1K_OUTPUT = 0.015
# Rough token-count assumptions per query, based on this system's actual
# prompt sizes (routing + consolidation + generation + verification, plus
# one specialist's own tool-use turns) - not measured per-call.
ESTIMATED_INPUT_TOKENS = 3500
ESTIMATED_OUTPUT_TOKENS = 400


def build_eval_set(products_table, n: int, seed: int = 42) -> list[dict]:
    """Real products with enough real review content to make a query
    synthesizable and a citation checkable - not synthetic fixtures."""
    candidates = [product_from_item(item) for item in scan_all_items(products_table) if int(item.get("review_count", 0)) >= 3 and item.get("brand")]
    random.Random(seed).shuffle(candidates)
    return [{"product_id": p.product_id, "title": p.title, "brand": p.brand, "category": p.category} for p in candidates[:n]]


def run_eval(api_url: str, products_table, n: int) -> list[dict]:
    query_client = BedrockClaudeClient(QUERY_MODEL_ID, region=REGION, max_tokens=60)
    judge_client = BedrockClaudeClient(JUDGE_MODEL_ID, region=REGION, max_tokens=200)

    eval_set = build_eval_set(products_table, n)
    results = []
    for i, product in enumerate(eval_set, start=1):
        question = query_client.summarize(build_query_prompt(product)).strip()
        print(f"[{i}/{len(eval_set)}] {product['product_id']}: {question!r}")

        start = time.monotonic()
        response = requests.post(api_url + "/query", json={"prompt": question}, timeout=90)
        latency_ms = (time.monotonic() - start) * 1000
        response.raise_for_status()
        body = response.json()

        cited_ids = [c["product_id"] for c in body.get("citations", [])]
        precision, hit = hit_at_k(cited_ids, product["product_id"])

        judge_raw = judge_client.summarize(build_judge_prompt(question, body.get("answer", ""), body.get("citations", [])))
        try:
            judge = parse_json_response(judge_raw)
        except (json.JSONDecodeError, AttributeError):
            judge = {"groundedness": None, "relevance": None, "notes": f"judge response unparseable: {judge_raw[:200]!r}"}

        results.append(
            {
                "product_id": product["product_id"],
                "question": question,
                "answer": body.get("answer", ""),
                "cited_product_ids": cited_ids,
                "dispatched": body.get("dispatched", []),
                "precision_at_k": precision,
                "hit_at_k": hit,
                "judge_groundedness": judge.get("groundedness"),
                "judge_relevance": judge.get("relevance"),
                "judge_notes": judge.get("notes"),
                "latency_ms": latency_ms,
            }
        )
    return results


def estimate_cost_per_query_usd() -> float:
    """A documented ESTIMATE, not a metered value - see the module
    docstring's pricing constants. The router's response doesn't currently
    report real token usage per call."""
    calls = [
        (HAIKU_PRICE_PER_1K_INPUT, HAIKU_PRICE_PER_1K_OUTPUT),  # routing (Haiku)
        (HAIKU_PRICE_PER_1K_INPUT, HAIKU_PRICE_PER_1K_OUTPUT),  # consolidation (Haiku)
        (SONNET_PRICE_PER_1K_INPUT, SONNET_PRICE_PER_1K_OUTPUT),  # generation (Sonnet)
        (HAIKU_PRICE_PER_1K_INPUT, HAIKU_PRICE_PER_1K_OUTPUT),  # verification (Haiku)
        (HAIKU_PRICE_PER_1K_INPUT, HAIKU_PRICE_PER_1K_OUTPUT),  # one specialist's tool-use turn (Haiku)
    ]
    return sum(
        (ESTIMATED_INPUT_TOKENS / 1000) * input_price + (ESTIMATED_OUTPUT_TOKENS / 1000) * output_price for input_price, output_price in calls
    )


def publish_metrics(results: list[dict], cost_per_query_usd: float) -> None:
    cloudwatch = boto3.client("cloudwatch", region_name=REGION)
    dimensions = [
        {"Name": "Model", "Value": "claude-sonnet-4-5"},
        {"Name": "AgentConfig", "Value": "multi-agent-router"},
        {"Name": "EmbeddingModel", "Value": "titan-text-v2+cohere-embed-v4"},
    ]
    grounded = [r["judge_groundedness"] for r in results if r["judge_groundedness"] is not None]
    relevant = [r["judge_relevance"] for r in results if r["judge_relevance"] is not None]

    def avg(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    # "CitationGroundingRate" per design doc 05 is claim-level (fraction of
    # claims actually supported), not "did the system find the right
    # product" (that's recall@k, published separately as RecallAtK). The
    # harness doesn't segment individual claims, so this is a documented
    # proxy: the fraction of answers the independent judge scored
    # predominantly grounded (>=4/5) - not a strict per-claim fraction.
    # See docs/experiments/06-production-eval.md.
    metrics = {
        "RetrievalPrecisionAtK": avg([r["precision_at_k"] for r in results]),
        "RecallAtK": avg([r["hit_at_k"] for r in results]),
        "CitationGroundingRate": sum(1 for g in grounded if g >= 4) / len(grounded) if grounded else 0.0,
        "JudgeGroundednessScore": avg(grounded),
        "JudgeRelevanceScore": avg(relevant),
        "AnswerLatencyMs": avg([r["latency_ms"] for r in results]),
        "CostPerQueryUsd": cost_per_query_usd,
    }
    cloudwatch.put_metric_data(
        Namespace="RAGEcommerce/Eval",
        MetricData=[{"MetricName": name, "Value": value, "Dimensions": dimensions} for name, value in metrics.items()],
    )
    print("\nPublished to CloudWatch RAGEcommerce/Eval:")
    for name, value in metrics.items():
        print(f"  {name}: {value:.3f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--products-table", required=True)
    parser.add_argument("--out", default="scripts/eval/results.json")
    args = parser.parse_args()

    products_table = boto3.resource("dynamodb", region_name=REGION).Table(args.products_table)
    results = run_eval(args.api_url, products_table, args.n)

    cost_per_query_usd = estimate_cost_per_query_usd()
    publish_metrics(results, cost_per_query_usd)

    Path(args.out).write_text(json.dumps(results, indent=2))
    print(f"\nRaw per-query results written to {args.out}")


if __name__ == "__main__":
    main()

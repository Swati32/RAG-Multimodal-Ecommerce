"""Compares summarization prompts via LLM-as-judge, per the evaluation
approach in docs/designs/05-observability-cost.md. Needs Bedrock model
access - blocked on this account as of 2026-09-18 (AccessDeniedException:
account verification in progress). Results feed
docs/experiments/02-summarization-prompting.md once it can run.

Generator: Claude Haiku - cheap/fast, matches the bulk ingestion-time use.
Judge: Claude Sonnet 5 - stronger model grading a weaker one, to avoid
self-grading bias (see design doc 05, "LLM-as-judge").
"""

import json

import boto3
import requests
from huggingface_hub import hf_hub_url

from prompt_candidates import CANDIDATES

REGION = "us-east-2"
GENERATOR_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
JUDGE_MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
SAMPLE_SIZE = 10
MIN_WORDS = 150

DATASET_REPO = "McAuley-Lab/Amazon-Reviews-2023"
REVIEWS_FILE = "raw/review_categories/All_Beauty.jsonl"

JUDGE_PROMPT = """You are grading a product review summary for a RAG ingestion pipeline.

Original review:
{review_text}

Summary to grade:
{summary}

Score the summary from 1-5 on each axis and respond with ONLY this JSON, no other text:
{{"groundedness": <1-5, does the summary avoid inventing claims not in the original>, "coverage": <1-5, does it retain the key concrete claims>, "reasoning": "<one sentence>"}}"""


def invoke(client, model_id: str, prompt: str, max_tokens: int = 300) -> str:
    response = client.invoke_model(
        modelId=model_id,
        body=json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
        ),
    )
    payload = json.loads(response["body"].read())
    return payload["content"][0]["text"]


def fetch_long_review_sample(n: int, min_words: int) -> list[str]:
    url = hf_hub_url(DATASET_REPO, REVIEWS_FILE, repo_type="dataset")
    sample = []
    with requests.get(url, stream=True) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            text = json.loads(line).get("text", "")
            if len(text.split()) >= min_words:
                sample.append(text)
            if len(sample) >= n:
                break
    return sample


def main() -> None:
    client = boto3.client("bedrock-runtime", region_name=REGION)
    sample = fetch_long_review_sample(SAMPLE_SIZE, MIN_WORDS)
    print(f"Scoring {len(CANDIDATES)} prompts against {len(sample)} reviews...\n")

    for name, template in CANDIDATES.items():
        scores = []
        for review_text in sample:
            summary = invoke(client, GENERATOR_MODEL_ID, template.format(review_text=review_text))
            judged = invoke(client, JUDGE_MODEL_ID, JUDGE_PROMPT.format(review_text=review_text, summary=summary))
            scores.append(json.loads(judged))

        avg_groundedness = sum(s["groundedness"] for s in scores) / len(scores)
        avg_coverage = sum(s["coverage"] for s in scores) / len(scores)
        print(f"{name}: groundedness={avg_groundedness:.2f} coverage={avg_coverage:.2f}")


if __name__ == "__main__":
    main()

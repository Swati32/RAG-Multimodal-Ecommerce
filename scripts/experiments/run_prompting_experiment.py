"""Compares summarization prompts via LLM-as-judge, per the evaluation
approach in docs/designs/05-observability-cost.md. Needs Bedrock model
access - blocked on this account as of 2026-09-18 (a one-time Anthropic
model use-case form, see docs/experiments/02-summarization-prompting.md).

Generator: Claude Haiku - cheap/fast, matches the bulk ingestion-time use.
Judge: a stronger model grading a weaker one, to avoid self-grading bias
(see design doc 05, "LLM-as-judge").
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from dataset_source import fetch_long_review_sample  # noqa: E402
from ingestion.summarization import BedrockClaudeClient  # noqa: E402
from prompt_candidates import CANDIDATES  # noqa: E402

REGION = "us-east-2"
GENERATOR_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
JUDGE_MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
SAMPLE_SIZE = 10
MIN_WORDS = 150

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _parse_judge_response(raw: str) -> dict:
    fenced = _JSON_FENCE.search(raw)
    return json.loads(fenced.group(1) if fenced else raw)

JUDGE_PROMPT = """You are grading a product review summary for a RAG ingestion pipeline.

Original review:
{review_text}

Summary to grade:
{summary}

Score the summary from 1-5 on each axis and respond with ONLY this JSON, no other text:
{{"groundedness": <1-5, does the summary avoid inventing claims not in the original>, "coverage": <1-5, does it retain the key concrete claims>, "reasoning": "<one sentence>"}}"""


def main() -> None:
    generator = BedrockClaudeClient(GENERATOR_MODEL_ID, region=REGION, max_tokens=300)
    judge = BedrockClaudeClient(JUDGE_MODEL_ID, region=REGION, max_tokens=300)

    sample = fetch_long_review_sample(SAMPLE_SIZE, MIN_WORDS)
    print(f"Scoring {len(CANDIDATES)} prompts against {len(sample)} reviews...\n")

    for name, template in CANDIDATES.items():
        scores = []
        for review_text in sample:
            summary = generator.summarize(template.format(review_text=review_text))
            judged = judge.summarize(JUDGE_PROMPT.format(review_text=review_text, summary=summary))
            scores.append(_parse_judge_response(judged))

        avg_groundedness = sum(s["groundedness"] for s in scores) / len(scores)
        avg_coverage = sum(s["coverage"] for s in scores) / len(scores)
        print(f"{name}: groundedness={avg_groundedness:.2f} coverage={avg_coverage:.2f}")


if __name__ == "__main__":
    main()

"""Pure/testable plumbing for the production evaluation harness - see
docs/designs/05-observability-cost.md, "Evaluation framework", and
docs/experiments/06-production-eval.md.

Distinct from scripts/experiments/retrieval_eval_utils.py: that ran
retrieval-only experiments against throwaway indexes to validate chunking/
indexing decisions before the agents existed. This evaluates the real
deployed system (the query API -> router -> specialists -> generator ->
verifier) end-to-end, against production data.
"""

from __future__ import annotations

import json
import re

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

QUERY_PROMPT = (
    "Here is a real product listing. Write ONE short, natural shopping "
    "question (5-15 words) a shopper would type to find this exact "
    "product - reference its category, brand, or a distinguishing "
    "feature, not its exact title verbatim. Respond with ONLY the "
    "question text, no quotes, no other text.\n\n"
    "Title: {title}\nBrand: {brand}\nCategory: {category}"
)

JUDGE_PROMPT = """You are an independent judge scoring one answer from a multi-agent product Q&A system. Score it fresh from the question/answer/citations given - don't assume the system's own citations are already correct, that's exactly what you're checking.

Question: {question}

Answer: {answer}

Citations (product_id + the snippet the system says supports the answer): {citations}

Score on a 1-5 scale:
- groundedness: does the answer's claims actually match what the citation snippets say, with nothing invented?
- relevance: does the answer actually address the question asked?

Respond with ONLY this JSON, no other text: {{"groundedness": <1-5>, "relevance": <1-5>, "notes": "<one sentence on the biggest issue, or \\"none\\">"}}"""


def parse_json_response(raw: str) -> dict:
    fenced = _JSON_FENCE.search(raw)
    return json.loads(fenced.group(1) if fenced else raw)


def build_query_prompt(product: dict) -> str:
    return QUERY_PROMPT.format(title=product["title"], brand=product.get("brand") or "unknown", category=product["category"])


def hit_at_k(cited_product_ids: list[str], expected_product_id: str) -> tuple[float, float]:
    """Returns (precision, hit) for one query, k = number of citations the
    system actually returned - see docs/designs/05-observability-cost.md
    for the metric definitions. The router's real output surfaces a
    citation list, not a separate raw top-k retrieval list, so that
    citation list is what "top k" means here."""
    k = max(len(cited_product_ids), 1)
    relevant = 1 if expected_product_id in cited_product_ids else 0
    return relevant / k, float(relevant)


def build_judge_prompt(question: str, answer: str, citations: list[dict]) -> str:
    citation_summaries = [{"product_id": c["product_id"], "snippet": c.get("snippet", "")} for c in citations]
    return JUDGE_PROMPT.format(question=question, answer=answer, citations=json.dumps(citation_summaries, indent=2))

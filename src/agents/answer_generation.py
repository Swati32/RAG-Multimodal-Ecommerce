"""Generator + verifier - direct LLM calls, not agentic, see docs/designs/
02-retrieval-agents.md, "Generator + verifier" and "Answer format". Called
by the router after consolidation, matching the design's sequence diagram
(RTR->>GEN->>VER) - these don't get their own AgentCore Runtime, since the
design's own "When to use an agent vs. a direct LLM call" reasoning is
explicit that final answer generation "doesn't need to be agentic, it
needs good context".
"""

from __future__ import annotations

import json

from agents.router_tools import converse_text, parse_json_response
from ingestion.dynamo_reader import product_from_item

GENERATOR_INSTRUCTION = """You are the final answer generator for a multi-agent e-commerce product Q&A system. You are given a shopper's question and a set of retrieved product/review records already gathered and ranked by the system - your job is to turn them into a helpful, grounded answer, citing only the specific products you actually rely on.

Rules:
- Every factual claim about a specific product must be traceable to one of the provided records - do not state a fact about a product that isn't supported by its record's content.
- Cite a product by its product_id, with a short snippet from that record's content that supports the claim - quote or closely paraphrase the actual record, don't invent a snippet.
- If the provided records don't actually answer the question, say so plainly rather than stretching an unrelated record into an answer.
- A product being present in the records isn't itself a reason to cite it - only cite one with a real, specific connection to a claim you're making.

Respond with ONLY this JSON, no other text: {"answer": "<the answer text>", "citations": [{"product_id": "<id>", "snippet": "<short supporting excerpt from that record>"}]}"""

VERIFIER_INSTRUCTION = """You are a verifier for a multi-agent e-commerce product Q&A system. You are given a set of citations - each claims a snippet is supported by a specific product's retrieved record - and the actual records. Check each citation strictly: is the snippet actually present in (or a faithful close paraphrase of) that product's record content, not just plausible-sounding?

Respond with ONLY this JSON, no other text: {"verdicts": [{"product_id": "<id>", "grounded": <true or false>}]} - exactly one verdict per citation, in the same order given."""


def generate_answer(bedrock, model_id: str, question: str, results: list[dict]) -> dict:
    prompt = f"Question: {question}\n\nRetrieved records:\n{json.dumps(results, indent=2)}"
    parsed = parse_json_response(converse_text(bedrock, model_id, GENERATOR_INSTRUCTION, prompt))
    return {"answer": parsed.get("answer", ""), "citations": parsed.get("citations", [])}


def verify_citations(bedrock, model_id: str, citations: list[dict], results: list[dict]) -> list[dict]:
    """Drops any citation the verifier can't confirm is actually grounded -
    targets "hallucinated confidence despite citations" (design doc 05):
    a citation naming a real product for an unsupported claim about it."""
    if not citations:
        return []

    records_by_id = {record["product_id"]: record for record in results if record.get("product_id")}
    cited_records = [records_by_id.get(citation["product_id"], {}) for citation in citations]
    prompt = f"Citations:\n{json.dumps(citations, indent=2)}\n\nRecords:\n{json.dumps(cited_records, indent=2)}"
    verdicts = parse_json_response(converse_text(bedrock, model_id, VERIFIER_INSTRUCTION, prompt)).get("verdicts", [])
    grounded_by_id = {verdict["product_id"]: verdict.get("grounded", False) for verdict in verdicts}
    return [citation for citation in citations if grounded_by_id.get(citation["product_id"], False)]


def resolve_citations(products_table, citations: list[dict]) -> list[dict]:
    """Deterministic post-processing outside the model, per design doc 02's
    Answer format section. `product_url` is a placeholder path
    (`/products/{id}`), not a real link - workflow 06 (Frontend Hosting)
    doesn't exist yet, so there's nowhere real to link to."""
    resolved = []
    for citation in citations:
        item = products_table.get_item(Key={"product_id": citation["product_id"]}).get("Item")
        if item is None:
            continue
        product = product_from_item(item)
        resolved.append(
            {
                "product_id": product.product_id,
                "title": product.title,
                "image_url": product.image_keys[0] if product.image_keys else None,
                "product_url": f"/products/{product.product_id}",
                "snippet": citation.get("snippet", ""),
            }
        )
    return resolved

"""Generator + verifier - direct LLM calls, not agentic, see docs/designs/
02-retrieval-agents.md, "Generator + verifier" and "Answer format", and
docs/designs/04-inference-serving.md, "final answer generation... streaming
to client". Called by the router after consolidation, matching the
design's sequence diagram (RTR->>GEN->>VER) - these don't get their own
AgentCore Runtime, since the design's own "When to use an agent vs. a
direct LLM call" reasoning is explicit that final answer generation
"doesn't need to be agentic, it needs good context".

The generator streams plain text rather than returning one JSON blob -
JSON doesn't stream usefully (a client can't do anything with a half-formed
object), so instead of asking Claude for a separate structured citations
field, citations are inline markers ([[product_id]]) the client sees as
part of the answer text itself, extracted with `extract_citations` once
streaming finishes. This is a deliberate deviation from the JSON answer
format design doc 02 originally shipped with - see design doc 04's
"Implementation notes" for the full reasoning.
"""

from __future__ import annotations

import json
import re

from boto3.dynamodb.conditions import Key

from agents.router_tools import converse_text, parse_json_response
from ingestion.dynamo_reader import product_from_item, review_from_item

GENERATOR_INSTRUCTION = """You are the final answer generator for a multi-agent e-commerce product Q&A system. You are given a shopper's question and a set of retrieved product/review records already gathered and ranked by the system - your job is to turn them into a helpful, grounded answer, citing only the specific products you actually rely on.

Write the answer as plain text for the shopper to read directly - not JSON, no markdown code fences.

Rules:
- Never state a product's name, title, or brand in your prose - refer to it generically instead ("one option", "another product", "this one"). The shopper sees the real product name on its own card right next to your answer; repeating it in the sentence is redundant. Product names belong on the card, not in your writing.
- Every factual claim about a specific product must be traceable to one of the provided records - do not state a fact about a product that isn't supported by its record's content.
- This is mandatory, not optional: every sentence or bullet that makes a claim about a specific product MUST end with a citation marker for that product's exact product_id: [[product_id]] - e.g. "One option is praised for gentle moisturizing. [[B0123456]]". A sentence about a product with no marker is a formatting error - re-check your answer before finishing and add any marker you missed.
- If the provided records don't actually answer the question, say so plainly rather than stretching an unrelated record into an answer - and don't insert any markers in that case.
- A product being present in the records isn't itself a reason to cite it - only mark a sentence with a real, specific connection to that product's record.
- If a shopper's image is attached, the records below are already the system's matches for it - answer directly from the records rather than asking to see the image or describing it back; the image is context, not something you need permission to proceed without."""

VERIFIER_INSTRUCTION = """You are a verifier for a multi-agent e-commerce product Q&A system. You are given a set of citations - each claims a snippet is supported by a specific product's retrieved record - and the actual records. Check each citation strictly: is the snippet actually present in (or a faithful close paraphrase of) that product's record content, not just plausible-sounding?

Respond with ONLY this JSON, no other text: {"verdicts": [{"product_id": "<id>", "grounded": <true or false>}]} - exactly one verdict per citation, in the same order given."""

_CITATION_MARKER = re.compile(r"\[\[([A-Za-z0-9]+)\]\]")


def stream_answer(bedrock, model_id: str, question: str, results: list[dict], image_bytes: bytes | None = None, image_format: str = "jpeg"):
    """Yields answer text deltas as Claude generates them (Bedrock Converse
    Stream), so a client can start rendering before the full answer is
    ready - design doc 04's "streaming improves perceived latency for a
    chat-style answer".

    `image_bytes`, when given, attaches the shopper's uploaded query image
    - the same real bug found in consolidation (see docs/designs/
    03-image-upload.md) also hit generation: a shopper's prompt like "find
    products similar to this image" made Claude refuse to answer at all
    ("I'm unable to see or process images directly"), even though the
    actual records to write from were already sitting right there.
    `image_format` must match the actual upload (jpeg/png/webp)."""
    prompt = f"Question: {question}\n\nRetrieved records:\n{json.dumps(results, indent=2)}"
    content = [{"image": {"format": image_format, "source": {"bytes": image_bytes}}}, {"text": prompt}] if image_bytes else [{"text": prompt}]
    response = bedrock.converse_stream(modelId=model_id, system=[{"text": GENERATOR_INSTRUCTION}], messages=[{"role": "user", "content": content}])
    for event in response["stream"]:
        delta = event.get("contentBlockDelta", {}).get("delta", {})
        if "text" in delta:
            yield delta["text"]


def extract_citations(answer_text: str, results: list[dict]) -> tuple[str, list[dict]]:
    """Parses the inline [[product_id]] markers the generator inserts (see
    GENERATOR_INSTRUCTION) into structured {"product_id", "snippet"}
    citations - the snippet is the sentence immediately before its marker,
    since the streamed answer text itself is now the source of the claim
    being cited, not a separately requested JSON field. Returns the answer
    with markers stripped (what the client should actually display) plus
    the extracted citations, ready for `verify_citations` unchanged."""
    citations = []
    clean_parts = []
    cursor = 0
    for match in _CITATION_MARKER.finditer(answer_text):
        preceding = answer_text[cursor : match.start()]
        clean_parts.append(preceding)
        # Split on sentence boundaries AND paragraph breaks - a marker's
        # claim shouldn't reach back across a "\n\n" into an unrelated
        # intro paragraph (seen for real: a first citation's snippet
        # swallowing the whole answer's opening line before this fix).
        sentences = re.split(r"(?<=[.!?])\s+|\n+", preceding.strip())
        claim = sentences[-1] if sentences else ""
        if claim:
            citations.append({"product_id": match.group(1), "snippet": claim})
        cursor = match.end()
        if cursor < len(answer_text) and answer_text[cursor] == " ":
            cursor += 1  # the space Claude puts between the marker and the next sentence
    clean_parts.append(answer_text[cursor:])
    # A marker's own preceding space stays in clean_parts (it belongs
    # between two sentences) but leaves a trailing space if that marker was
    # the last thing in the answer - the common case, since the final
    # sentence is often the one being cited.
    return "".join(clean_parts).rstrip(), citations


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


_REVIEWS_PER_CARD = 2


def resolve_citations(
    products_table, reviews_table, citations: list[dict], reasons_by_id: dict[str, str] | None = None
) -> list[dict]:
    """Deterministic post-processing outside the model, per design doc 02's
    Answer format section. `product_url` is a placeholder path
    (`/products/{id}`), not a real link - workflow 06 (Frontend Hosting)
    doesn't exist yet, so there's nowhere real to link to.

    Attaches a couple of real reviews per product (a single-partition
    Reviews query, cheap - the table's key is (product_id, review_id), see
    docs/designs/01-ingestion-pipeline.md) and, when given, the
    consolidation step's own reasoning for why this product was ranked
    where it was (`reasons_by_id`, from `run_consolidation` in
    router_runtime.py) - both shown on the product card in the frontend,
    not in the generated prose (see GENERATOR_INSTRUCTION's "never state a
    product's name... in your prose")."""
    reasons_by_id = reasons_by_id or {}
    resolved = []
    for citation in citations:
        item = products_table.get_item(Key={"product_id": citation["product_id"]}).get("Item")
        if item is None:
            continue
        product = product_from_item(item)
        review_items = reviews_table.query(KeyConditionExpression=Key("product_id").eq(product.product_id), Limit=_REVIEWS_PER_CARD).get(
            "Items", []
        )
        reviews = [{"rating": review_from_item(review_item).rating, "text": review_from_item(review_item).text} for review_item in review_items]
        resolved.append(
            {
                "product_id": product.product_id,
                "title": product.title,
                "brand": product.brand,
                "image_url": product.image_keys[0] if product.image_keys else None,
                "product_url": f"/products/{product.product_id}",
                "snippet": citation.get("snippet", ""),
                "rank_reason": reasons_by_id.get(product.product_id, ""),
                "reviews": reviews,
            }
        )
    return resolved

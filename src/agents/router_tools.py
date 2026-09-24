"""Router's dispatch + consolidation helpers - see docs/designs/
02-retrieval-agents.md, "Architecture" and "Consolidating specialist
results: LLM-judged, then deterministically deduped".
"""

from __future__ import annotations

import json
import re
import uuid

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def parse_json_response(raw: str) -> dict:
    """Claude sometimes wraps JSON in a markdown fence despite being told
    not to - see CLAUDE.md."""
    fenced = _JSON_FENCE.search(raw)
    return json.loads(fenced.group(1) if fenced else raw)


def converse_text(bedrock, model_id: str, instruction: str, prompt: str) -> str:
    """One-shot Converse call, text only - the shared shape behind every
    JSON-output Claude call in the router/generator/verifier (routing
    decision, consolidation, answer generation, citation verification)."""
    response = bedrock.converse(modelId=model_id, system=[{"text": instruction}], messages=[{"role": "user", "content": [{"text": prompt}]}])
    return next(block["text"] for block in response["output"]["message"]["content"] if "text" in block)


def dispatch_specialist(agentcore_client, agent_runtime_arn: str, payload: dict) -> list[dict]:
    response = agentcore_client.invoke_agent_runtime(
        agentRuntimeArn=agent_runtime_arn,
        runtimeSessionId=f"router-{uuid.uuid4()}",
        payload=json.dumps(payload).encode(),
    )
    body = json.loads(response["response"].read())
    return body.get("results", [])


def dedupe_and_rank(candidates: list[dict], ranked_product_ids: list[str]) -> list[dict]:
    """Deterministic post-processing, not left to the LLM - one record per
    product_id, kept in the LLM-judged rank order. Different specialists
    return different field shapes for the same product (a full product
    record from LookupAgent vs. a chunk snippet from SearchAgent vs. an
    image record from ImageAgent) - this keeps whichever record was seen
    first rather than merging fields, a real simplification worth
    revisiting once the generator's actual field needs are known."""
    by_product_id: dict[str, dict] = {}
    for candidate in candidates:
        product_id = candidate.get("product_id")
        if product_id and product_id not in by_product_id:
            by_product_id[product_id] = candidate
    return [by_product_id[pid] for pid in ranked_product_ids if pid in by_product_id]

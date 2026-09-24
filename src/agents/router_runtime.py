"""AgentCore Runtime entrypoint for the router - decides which specialist
agents apply, dispatches them in parallel, LLM-judges + deterministically
dedupes their results, then generates and verifies the final answer. See
docs/designs/02-retrieval-agents.md, "Architecture", "Consolidating
specialist results", and "Generator + verifier".

Doesn't use tool_loop.py's Claude-tool-use loop the way the four
specialists do: the router's shape is several distinct phases (routing,
consolidation, generation, verification), not one open-ended "call a tool,
see the result, maybe call another" loop. Each phase is one focused,
single-turn JSON-output Claude call instead - this keeps "which specialists
ran," "the judged ranking," and "the final answer" as clearly separate
outputs rather than interleaved tool_use/toolResult turns, and lets
dispatch actually run concurrently (ThreadPoolExecutor) instead of relying
on however Converse happens to batch tool_use blocks in one turn.

Generator and verifier are direct LLM calls, not their own AgentCore
Runtime - the design's own "When to use an agent vs. a direct LLM call"
reasoning is explicit that final answer generation "doesn't need to be
agentic, it needs good context."
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from botocore.exceptions import ConnectTimeoutError, ReadTimeoutError

from agents.answer_generation import extract_citations, resolve_citations, stream_answer, verify_citations
from agents.bedrock_client import agentcore_client, bedrock_runtime_client
from agents.router_tools import converse_text, dedupe_and_rank, dispatch_specialist, parse_json_response

REGION = "us-east-2"
ROUTING_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
GENERATOR_MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
# Cheaper than the generator, on purpose - verifying a claim is grounded is
# a strictly easier task than drafting the answer, per design doc 02's
# "Generator + verifier" ("a second, cheaper Claude call verifies...").
VERIFIER_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

SPECIALIST_ARN_ENV_VARS = {
    "search_agent": "SEARCH_AGENT_ARN",
    "graph_agent": "GRAPH_AGENT_ARN",
    "lookup_agent": "LOOKUP_AGENT_ARN",
    "image_agent": "IMAGE_AGENT_ARN",
}

ROUTING_INSTRUCTION = """You are the router for a multi-agent e-commerce product Q&A system. Given a shopper's question, decide which specialist retrieval agents are relevant - only the ones that actually apply, not every one "just in case".

Specialists:
- search_agent: semantic/keyword search over product descriptions and reviews, with optional price/rating/brand/category filters. Use for open-ended product questions ("gentle moisturizer under $20", "headphones with good bass").
- graph_agent: finds products related to ONE SPECIFIC other product already named or implied - same brand, same category, or "customers who reviewed this also reviewed" (use for "what pairs with X", "what else does this brand make").
- lookup_agent: fetches one exact product or review record by its exact id, when the shopper gives a specific product_id or review_id directly.
- image_agent: visual similarity search from an uploaded photo - only relevant if a photo was provided.

Respond with ONLY this JSON, no other text: {"search_agent": <bool>, "graph_agent": <bool>, "lookup_agent": <bool>, "image_agent": <bool>}"""

CONSOLIDATION_INSTRUCTION = """You are consolidating results from multiple retrieval specialists in an e-commerce product Q&A system into one ranked list of the most relevant products for the shopper's question. The specialists don't share a comparable relevance score - judge relevance yourself from each candidate's content, not any numeric score field.

Respond with ONLY this JSON, no other text: {"ranked_product_ids": [<product_id strings, most relevant first>]}. Include a product_id at most once, and omit any candidate that isn't actually relevant to the question - do not pad the list to include everything."""

app = BedrockAgentCoreApp()
_bedrock = bedrock_runtime_client(REGION)
_agentcore = agentcore_client(REGION)
_dynamodb = boto3.resource("dynamodb", region_name=REGION)


def decide_dispatch(bedrock, question: str, has_image: bool) -> dict:
    image_note = "An image was provided with this question." if has_image else "No image was provided."
    decision = parse_json_response(
        converse_text(bedrock, ROUTING_MODEL_ID, ROUTING_INSTRUCTION, f"{image_note}\n\nQuestion: {question}")
    )
    if not has_image:
        decision["image_agent"] = False  # never dispatch it with nothing to search with, regardless of what Claude decided
    return decision


def run_consolidation(bedrock, question: str, candidates: list[dict]) -> list[str]:
    prompt = f"Question: {question}\n\nCandidates:\n{json.dumps(candidates, indent=2)}"
    return parse_json_response(converse_text(bedrock, ROUTING_MODEL_ID, CONSOLIDATION_INSTRUCTION, prompt)).get(
        "ranked_product_ids", []
    )


@app.entrypoint
def invoke(payload: dict):
    """A generator, not a plain function - `bedrock_agentcore`'s
    BedrockAgentCoreApp streams a generator's yielded values back to the
    caller as server-sent events (see docs/designs/04-inference-serving.md,
    "streaming to client"). Every path yields exactly one `{"type":
    "final", ...}` event as its last event, carrying the same shape the
    old plain-dict return used to - the only genuinely streamed part is the
    answer text itself, via `{"type": "answer_chunk", "text": ...}` events
    while the generator call runs."""
    question = payload.get("prompt", "")
    image_base64 = payload.get("image_base64")

    dispatch_decision = decide_dispatch(_bedrock, question, has_image=bool(image_base64))
    specialist_payloads = {
        "search_agent": {"prompt": question},
        "graph_agent": {"prompt": question},
        "lookup_agent": {"prompt": question},
        "image_agent": {"prompt": question, "image_base64": image_base64},
    }
    to_dispatch = [name for name, should_dispatch in dispatch_decision.items() if should_dispatch]

    candidates: list[dict] = []
    if to_dispatch:
        with ThreadPoolExecutor(max_workers=len(to_dispatch)) as pool:
            futures = {
                name: pool.submit(
                    dispatch_specialist, _agentcore, os.environ[SPECIALIST_ARN_ENV_VARS[name]], specialist_payloads[name]
                )
                for name in to_dispatch
            }
            for name, future in futures.items():
                try:
                    candidates.extend(future.result())
                except (ReadTimeoutError, ConnectTimeoutError):
                    # One specialist hanging shouldn't fail the whole
                    # request - proceed with whatever the others returned,
                    # per design doc 04's "Retry/fallback".
                    app.logger.warning("Specialist %s timed out, proceeding without it", name)

    if not candidates:
        answer = (
            "None of the specialists found anything relevant to your question."
            if to_dispatch
            else "I couldn't determine which part of the catalog applies to this question."
        )
        yield {"type": "final", "answer": answer, "citations": [], "dispatched": to_dispatch}
        return

    ranked_product_ids = run_consolidation(_bedrock, question, candidates)
    results = dedupe_and_rank(candidates, ranked_product_ids)

    if not results:
        yield {
            "type": "final",
            "answer": "I found some information but none of it was actually relevant to your question.",
            "citations": [],
            "dispatched": to_dispatch,
        }
        return

    answer_text = ""
    for chunk in stream_answer(_bedrock, GENERATOR_MODEL_ID, question, results):
        answer_text += chunk
        yield {"type": "answer_chunk", "text": chunk}

    clean_answer, raw_citations = extract_citations(answer_text, results)
    verified_citations = verify_citations(_bedrock, VERIFIER_MODEL_ID, raw_citations, results)
    resolved_citations = resolve_citations(_dynamodb.Table(os.environ["PRODUCTS_TABLE"]), verified_citations)

    yield {"type": "final", "answer": clean_answer, "citations": resolved_citations, "dispatched": to_dispatch}


if __name__ == "__main__":
    app.run()

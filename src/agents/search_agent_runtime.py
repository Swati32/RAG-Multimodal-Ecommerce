"""AgentCore Runtime entrypoint for SearchAgent - OpenSearch hybrid search
over product descriptions/reviews/tags, with structured filters. See
docs/designs/02-retrieval-agents.md, SearchAgent, and lookup_agent_runtime.py
for the shared AgentCore/tool-loop pattern this follows.
"""

from __future__ import annotations

import json
import os

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection

from agents.bedrock_client import bedrock_runtime_client
from agents.search_tools import DEFAULT_K, ensure_search_pipeline, search_products
from agents.tool_loop import run_tool_loop

REGION = "us-east-2"
MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
INDEX_NAME = "chunks"
MAX_TURNS = 4

INSTRUCTION = """You are SearchAgent, a specialist in a multi-agent product Q&A system for an e-commerce catalog. Your job is to translate a shopper's question into a search over product descriptions, reviews, and tags - extracting any structured constraints so they're applied as real filters instead of left as text for the search to guess at.

Rules:
- Call search_products with:
  - query_text: the semantic/descriptive part of the question (what the product should be, do, or feel like) - features, use case, qualities like "good bass" or "gentle on skin". Do not put a fact into query_text if a specific filter field exists for it.
  - category / brand: only when the shopper names one explicitly and exactly - never guess one from context.
  - min_price / max_price: from phrases like "under $50", "between $20 and $40", "at least $10".
  - min_rating: from phrases like "4 stars and up", "highly rated".
  - in_stock: true only when the shopper explicitly asks for in-stock items.
- If the first search returns few or no relevant-looking results, you may try again once with a broader query_text or relaxed filters before giving up - do not loop indefinitely.
- You have no other capabilities - if the request isn't a product search, say so."""

TOOLS = [
    {
        "toolSpec": {
            "name": "search_products",
            "description": "Hybrid text + semantic search over product descriptions, reviews, and tags, with optional structured filters.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "query_text": {"type": "string", "description": "The semantic/descriptive search text."},
                        "category": {"type": "string", "description": "Exact category name, only if explicitly named."},
                        "brand": {"type": "string", "description": "Exact brand name, only if explicitly named."},
                        "min_price": {"type": "number"},
                        "max_price": {"type": "number"},
                        "min_rating": {"type": "number", "description": "Minimum average rating, e.g. 4 for '4 stars and up'."},
                        "in_stock": {"type": "boolean"},
                    },
                    "required": ["query_text"],
                }
            },
        }
    },
]

app = BedrockAgentCoreApp()
_bedrock = bedrock_runtime_client(REGION)


def _opensearch_client() -> OpenSearch:
    credentials = boto3.Session().get_credentials()
    auth = AWSV4SignerAuth(credentials, REGION, "es")
    return OpenSearch(
        hosts=[{"host": os.environ["OPENSEARCH_ENDPOINT"], "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
    )


def _embed(runtime, text: str) -> list[float]:
    response = runtime.invoke_model(modelId=EMBEDDING_MODEL_ID, body=json.dumps({"inputText": text}))
    return json.loads(response["body"].read())["embedding"]


_opensearch: OpenSearch | None = None


def get_opensearch_client() -> OpenSearch:
    """Lazy, cached - a module-level client would hit the network (and
    require OPENSEARCH_ENDPOINT) at import time, breaking tests and cold
    starts alike."""
    global _opensearch
    if _opensearch is None:
        _opensearch = _opensearch_client()
        ensure_search_pipeline(_opensearch)
    return _opensearch


def run_tool(name: str, tool_input: dict) -> list[dict] | dict:
    if name != "search_products":
        return {"error": f"Unknown tool: {name}"}

    query_text = tool_input.get("query_text", "")
    embedding = _embed(_bedrock, query_text)
    return search_products(
        get_opensearch_client(),
        INDEX_NAME,
        query_text,
        embedding,
        category=tool_input.get("category"),
        brand=tool_input.get("brand"),
        min_price=tool_input.get("min_price"),
        max_price=tool_input.get("max_price"),
        min_rating=tool_input.get("min_rating"),
        in_stock=tool_input.get("in_stock"),
        k=DEFAULT_K,
    )


@app.entrypoint
def invoke(payload: dict) -> dict:
    return run_tool_loop(_bedrock, MODEL_ID, INSTRUCTION, TOOLS, run_tool, payload.get("prompt", ""), MAX_TURNS)


if __name__ == "__main__":
    app.run()

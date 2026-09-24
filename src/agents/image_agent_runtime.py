"""AgentCore Runtime entrypoint for ImageAgent - k-NN search over the
product_images index from an uploaded photo. See docs/designs/
02-retrieval-agents.md, ImageAgent, and lookup_agent_runtime.py for the
shared AgentCore/tool-loop pattern this follows.

Unlike SearchAgent, the image is embedded eagerly (before the loop even
starts), not lazily inside the tool call: an uploaded photo has no
"extraction" step the way natural-language text needs Claude to pull a
query out of it - the image already *is* the complete query, so there's
nothing to wait on Claude to decide first. `run_tool` is built as a
closure per request (not a fixed module-level function like the other
agents') because it needs that request's embedding, which Claude never
sees or echoes back through a tool call's arguments.
"""

from __future__ import annotations

import base64
import json
import os

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection

from agents.bedrock_client import bedrock_runtime_client
from agents.image_tools import DEFAULT_K, search_similar_images
from agents.tool_loop import run_tool_loop

REGION = "us-east-2"
MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
EMBEDDING_MODEL_ID = "us.cohere.embed-v4:0"
INDEX_NAME = "product_images"
MAX_TURNS = 4

INSTRUCTION = """You are ImageAgent, a specialist in a multi-agent product Q&A system for an e-commerce catalog. A shopper has uploaded a photo - your job is to find visually similar products, optionally narrowed by any structured constraints mentioned in their accompanying text.

Rules:
- Call find_visually_similar_products with any filters mentioned in the shopper's text:
  - category / brand: only when named explicitly and exactly - never guess one from what the image looks like.
  - min_price / max_price: from phrases like "under $50", "between $20 and $40", "at least $10".
  - min_rating: from phrases like "4 stars and up", "highly rated".
  - in_stock: true only when explicitly asked for in-stock items.
- If no text accompanies the image, or it mentions no constraints, call the tool with no filters - the image itself is the whole query.
- You have no other capabilities - if the request isn't about finding visually similar products, say so."""

TOOLS = [
    {
        "toolSpec": {
            "name": "find_visually_similar_products",
            "description": "k-NN search over product images for items that look like the shopper's uploaded photo, with optional structured filters.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "Exact category name, only if explicitly named."},
                        "brand": {"type": "string", "description": "Exact brand name, only if explicitly named."},
                        "min_price": {"type": "number"},
                        "max_price": {"type": "number"},
                        "min_rating": {"type": "number", "description": "Minimum average rating, e.g. 4 for '4 stars and up'."},
                        "in_stock": {"type": "boolean"},
                    },
                },
            },
        }
    },
]

app = BedrockAgentCoreApp()
_bedrock = bedrock_runtime_client(REGION)
_opensearch: OpenSearch | None = None


def get_opensearch_client() -> OpenSearch:
    global _opensearch
    if _opensearch is None:
        credentials = boto3.Session().get_credentials()
        auth = AWSV4SignerAuth(credentials, REGION, "es")
        _opensearch = OpenSearch(
            hosts=[{"host": os.environ["OPENSEARCH_ENDPOINT"], "port": 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
        )
    return _opensearch


def embed_image(runtime, image_bytes: bytes) -> list[float]:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    body = json.dumps(
        {
            "images": [f"data:image/jpeg;base64,{b64}"],
            "input_type": "image",
            "embedding_types": ["float"],
            "output_dimension": 1024,
        }
    )
    response = runtime.invoke_model(modelId=EMBEDDING_MODEL_ID, body=body)
    return json.loads(response["body"].read())["embeddings"]["float"][0]


@app.entrypoint
def invoke(payload: dict) -> dict:
    image_bytes = base64.b64decode(payload["image_base64"])
    embedding = embed_image(_bedrock, image_bytes)
    prompt_text = payload.get("prompt", "Find products that look like this.")

    def run_tool(name: str, tool_input: dict) -> list[dict] | dict:
        if name != "find_visually_similar_products":
            return {"error": f"Unknown tool: {name}"}
        return search_similar_images(
            get_opensearch_client(),
            INDEX_NAME,
            embedding,
            category=tool_input.get("category"),
            brand=tool_input.get("brand"),
            min_price=tool_input.get("min_price"),
            max_price=tool_input.get("max_price"),
            min_rating=tool_input.get("min_rating"),
            in_stock=tool_input.get("in_stock"),
            k=DEFAULT_K,
        )

    user_content = [
        {"image": {"format": "jpeg", "source": {"bytes": image_bytes}}},
        {"text": prompt_text},
    ]
    return run_tool_loop(_bedrock, MODEL_ID, INSTRUCTION, TOOLS, run_tool, user_content, MAX_TURNS)


if __name__ == "__main__":
    app.run()

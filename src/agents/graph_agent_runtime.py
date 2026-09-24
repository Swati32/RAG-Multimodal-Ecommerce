"""AgentCore Runtime entrypoint for GraphAgent - DynamoDB adjacency-list
traversal over GraphEdges. See docs/designs/02-retrieval-agents.md,
GraphAgent, and lookup_agent_runtime.py for the shared AgentCore/tool-loop
pattern this follows.
"""

from __future__ import annotations

import os

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp

from agents.graph_tools import find_related_products
from agents.tool_loop import run_tool_loop

REGION = "us-east-2"
MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
MAX_TURNS = 4

INSTRUCTION = """You are GraphAgent, a specialist in a multi-agent product Q&A system for an e-commerce catalog. Your job is to find products connected to a given product through the catalog's relationship graph - not by searching text.

Rules:
- Call find_related_products with the product_id and exactly one relation:
  - "co_reviewed": products that people who reviewed this product also reviewed - the closest signal this catalog has to "customers who bought this also liked", useful for "what pairs well with this" or "what else would go with this" questions.
  - "same_brand": other products from the same brand.
  - "same_category": other products in the same category.
- Pick the relation that matches what the shopper is actually asking - do not call all three "just in case" or guess a relation from vague wording.
- You only work from a product_id you already have - if the shopper hasn't given or implied a specific product, say you need one to traverse from.
- You have no other capabilities - if the request isn't about finding related products, say so."""

TOOLS = [
    {
        "toolSpec": {
            "name": "find_related_products",
            "description": "Find products connected to a given product via the catalog's relationship graph (co-reviewed, same brand, or same category).",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "product_id": {"type": "string"},
                        "relation": {"type": "string", "enum": ["co_reviewed", "same_brand", "same_category"]},
                    },
                    "required": ["product_id", "relation"],
                }
            },
        }
    },
]

app = BedrockAgentCoreApp()
_dynamodb = boto3.resource("dynamodb", region_name=REGION)
_bedrock = boto3.client("bedrock-runtime", region_name=REGION)


def run_tool(name: str, tool_input: dict) -> list[dict] | dict:
    if name != "find_related_products":
        return {"error": f"Unknown tool: {name}"}

    table = _dynamodb.Table(os.environ["GRAPH_EDGES_TABLE"])
    return find_related_products(table, tool_input.get("product_id"), tool_input.get("relation"))


@app.entrypoint
def invoke(payload: dict) -> dict:
    return run_tool_loop(_bedrock, MODEL_ID, INSTRUCTION, TOOLS, run_tool, payload.get("prompt", ""), MAX_TURNS)


if __name__ == "__main__":
    app.run()

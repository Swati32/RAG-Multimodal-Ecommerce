"""AgentCore Runtime entrypoint for LookupAgent - a hand-rolled Claude
tool-use loop, not AWS Bedrock Agents (classic): that service is in
maintenance mode and closed to new accounts (confirmed via a real
CreateAgent call), see docs/designs/02-retrieval-agents.md, "Agent
implementation". Hosted on AgentCore Runtime per that same doc.

Returns structured results (see tool_loop.py), not a prose answer - this
agent's output feeds the router's consolidation step, not the end user.
"""

from __future__ import annotations

import os

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp

from agents.lookup_tools import get_product_response, get_review_response
from agents.tool_loop import run_tool_loop

REGION = "us-east-2"
MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
MAX_TURNS = 4

INSTRUCTION = """You are LookupAgent, a specialist in a multi-agent product Q&A system for an e-commerce catalog. Your only job is direct, exact retrieval of one specific product or review record when given its id - you do not search, filter, rank, or guess.

Rules:
- If asked for a product, call get_product with the exact product_id given. Return its fields as-is.
- If asked for a review, call get_review with both the product_id and review_id given - reviews cannot be looked up by review_id alone, both ids are required.
- If no id is given, or the id doesn't exist in the system, say so plainly. Never invent or substitute a different id, and never guess at values you weren't given.
- You have no other capabilities - if the request isn't a direct id lookup, say this agent only handles direct id lookups."""

TOOLS = [
    {
        "toolSpec": {
            "name": "get_product",
            "description": "Fetch one product's full record by its exact product_id.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {"product_id": {"type": "string"}},
                    "required": ["product_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_review",
            "description": "Fetch one review's full record by its exact product_id and review_id.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {"product_id": {"type": "string"}, "review_id": {"type": "string"}},
                    "required": ["product_id", "review_id"],
                }
            },
        }
    },
]

app = BedrockAgentCoreApp()
_dynamodb = boto3.resource("dynamodb", region_name=REGION)
_bedrock = boto3.client("bedrock-runtime", region_name=REGION)


def run_tool(name: str, tool_input: dict) -> dict:
    if name == "get_product":
        table = _dynamodb.Table(os.environ["PRODUCTS_TABLE"])
        return get_product_response(table, tool_input.get("product_id"))
    if name == "get_review":
        table = _dynamodb.Table(os.environ["REVIEWS_TABLE"])
        return get_review_response(table, tool_input.get("product_id"), tool_input.get("review_id"))
    return {"error": f"Unknown tool: {name}"}


@app.entrypoint
def invoke(payload: dict) -> dict:
    return run_tool_loop(_bedrock, MODEL_ID, INSTRUCTION, TOOLS, run_tool, payload.get("prompt", ""), MAX_TURNS)


if __name__ == "__main__":
    app.run()

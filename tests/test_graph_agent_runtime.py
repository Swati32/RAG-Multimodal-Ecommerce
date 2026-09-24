import boto3
import pytest
from moto import mock_aws

import agents.graph_agent_runtime as runtime
from agents.tool_loop import run_tool_loop
from ingestion.graph_edges import brand_edges
from ingestion.graph_writer import upsert_edge
from ingestion.models import Product


class StubBedrockClient:
    def __init__(self, responses: list[dict]):
        self._responses = list(responses)
        self.calls = 0

    def converse(self, **kwargs):
        self.calls += 1
        return self._responses.pop(0)


def _tool_use_response(tool_use_id: str, name: str, input_: dict) -> dict:
    return {
        "output": {"message": {"role": "assistant", "content": [{"toolUse": {"toolUseId": tool_use_id, "name": name, "input": input_}}]}},
        "stopReason": "tool_use",
    }


def _final_response(text: str) -> dict:
    return {"output": {"message": {"role": "assistant", "content": [{"text": text}]}}, "stopReason": "end_turn"}


@pytest.fixture
def graph_edges_table(monkeypatch):
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-2")
        table = dynamodb.create_table(
            TableName="GraphEdges",
            KeySchema=[{"AttributeName": "node", "KeyType": "HASH"}, {"AttributeName": "edge", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "node", "AttributeType": "S"}, {"AttributeName": "edge", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        p1 = Product(product_id="P1", title="A", category="Electronics", category_path=["Electronics"], price=10, avg_rating=4, review_count=1, description="d", brand="Acme")
        p2 = Product(product_id="P2", title="B", category="Electronics", category_path=["Electronics"], price=10, avg_rating=4, review_count=1, description="d", brand="Acme")
        for product in (p1, p2):
            for node, edge in brand_edges(product):
                upsert_edge(table, node, edge)

        monkeypatch.setenv("GRAPH_EDGES_TABLE", "GraphEdges")
        monkeypatch.setattr(runtime, "_dynamodb", dynamodb)
        yield table


def _invoke(bedrock, user_message: str) -> dict:
    return run_tool_loop(bedrock, runtime.MODEL_ID, runtime.INSTRUCTION, runtime.TOOLS, runtime.run_tool, user_message, runtime.MAX_TURNS)


def test_returns_related_products_for_a_valid_relation(graph_edges_table):
    bedrock = StubBedrockClient(
        [_tool_use_response("t1", "find_related_products", {"product_id": "P1", "relation": "same_brand"}), _final_response("Found related products.")]
    )

    response = _invoke(bedrock, "What other Acme products do you have, given product P1?")

    assert response["results"] == [{"product_id": "P2", "relation": "same_brand"}]


def test_returns_no_results_and_a_message_when_claude_declines(graph_edges_table):
    bedrock = StubBedrockClient([_final_response("I need a specific product to traverse from.")])

    response = _invoke(bedrock, "What products are related to good headphones in general?")

    assert response["results"] == []
    assert response["message"] == "I need a specific product to traverse from."


def test_run_tool_rejects_unknown_tool_name(graph_edges_table):
    assert runtime.run_tool("delete_everything", {}) == {"error": "Unknown tool: delete_everything"}

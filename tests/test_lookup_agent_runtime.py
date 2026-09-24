import boto3
import pytest
from moto import mock_aws

import agents.lookup_agent_runtime as runtime
from ingestion.dynamo_writer import upsert_product
from ingestion.models import Product


class StubBedrockClient:
    """Queues scripted Converse API responses, matching the injected-stub
    pattern used for Claude calls elsewhere (see test_summarization.py)."""

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
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": "end_turn",
    }


@pytest.fixture
def products_table(monkeypatch):
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-2")
        table = dynamodb.create_table(
            TableName="Products",
            KeySchema=[{"AttributeName": "product_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "product_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        upsert_product(
            table,
            Product(
                product_id="P1",
                title="Wireless Headphones",
                category="Electronics",
                category_path=["Electronics"],
                price=49.99,
                avg_rating=4.5,
                review_count=1,
                description="Great sound.",
                brand="Acme",
            ),
        )
        monkeypatch.setenv("PRODUCTS_TABLE", "Products")
        monkeypatch.setattr(runtime, "_dynamodb", dynamodb)
        yield table


def _invoke(bedrock, user_message: str) -> dict:
    from agents.tool_loop import run_tool_loop

    return run_tool_loop(bedrock, runtime.MODEL_ID, runtime.INSTRUCTION, runtime.TOOLS, runtime.run_tool, user_message, runtime.MAX_TURNS)


def test_returns_structured_tool_result_for_a_valid_lookup(products_table):
    bedrock = StubBedrockClient(
        [
            _tool_use_response("t1", "get_product", {"product_id": "P1"}),
            _final_response("Found it."),
        ]
    )

    response = _invoke(bedrock, "Look up product P1.")

    assert response["results"] == [{"product_id": "P1", "title": "Wireless Headphones", "category": "Electronics", "category_path": ["Electronics"], "price": 49.99, "avg_rating": 4.5, "review_count": 1, "description": "Great sound.", "image_keys": [], "brand": "Acme", "in_stock": None}]
    assert response["message"] == "Found it."


def test_returns_no_results_and_a_message_when_claude_declines(products_table):
    bedrock = StubBedrockClient([_final_response("I can only look up items by exact id.")])

    response = _invoke(bedrock, "What's the best headphone?")

    assert response["results"] == []
    assert response["message"] == "I can only look up items by exact id."


def test_stops_after_max_turns_with_whatever_was_collected(products_table):
    bedrock = StubBedrockClient(
        [_tool_use_response(f"t{i}", "get_product", {"product_id": "P1"}) for i in range(runtime.MAX_TURNS)]
    )

    response = _invoke(bedrock, "Look up product P1.")

    assert len(response["results"]) == runtime.MAX_TURNS
    assert "turn limit" in response["message"]

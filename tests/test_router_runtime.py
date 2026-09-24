import json

import boto3
import pytest
from botocore.exceptions import ReadTimeoutError
from moto import mock_aws

import agents.router_runtime as runtime
from ingestion.dynamo_writer import upsert_product
from ingestion.models import Product


class StubBedrockClient:
    """Queues scripted Converse text responses - matches the injected-stub
    pattern used for Claude calls elsewhere (see test_summarization.py)."""

    def __init__(self, texts: list[str]):
        self._texts = list(texts)
        self.calls = 0

    def converse(self, **kwargs):
        self.calls += 1
        return {"output": {"message": {"content": [{"text": self._texts.pop(0)}]}}}


class _StubStreamingBody:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()


class StubAgentCoreClient:
    def __init__(self, results_by_arn: dict[str, list[dict]], timeout_arns: set[str] = frozenset()):
        self._results_by_arn = results_by_arn
        self._timeout_arns = timeout_arns
        self.invoked_arns = []

    def invoke_agent_runtime(self, **kwargs):
        arn = kwargs["agentRuntimeArn"]
        self.invoked_arns.append(arn)
        if arn in self._timeout_arns:
            raise ReadTimeoutError(endpoint_url="https://bedrock-agentcore.us-east-2.amazonaws.com")
        return {"response": _StubStreamingBody({"results": self._results_by_arn.get(arn, []), "message": "ok"})}


@pytest.fixture
def arn_env(monkeypatch):
    monkeypatch.setenv("SEARCH_AGENT_ARN", "arn:search")
    monkeypatch.setenv("GRAPH_AGENT_ARN", "arn:graph")
    monkeypatch.setenv("LOOKUP_AGENT_ARN", "arn:lookup")
    monkeypatch.setenv("IMAGE_AGENT_ARN", "arn:image")


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
                product_id="P2",
                title="Wireless Headphones",
                category="Electronics",
                category_path=["Electronics"],
                price=49.99,
                avg_rating=4.5,
                review_count=1,
                description="Great sound.",
                image_keys=["https://example.com/p2.jpg"],
                brand="Acme",
            ),
        )
        monkeypatch.setenv("PRODUCTS_TABLE", "Products")
        monkeypatch.setattr(runtime, "_dynamodb", dynamodb)
        yield table


def test_decide_dispatch_forces_image_agent_off_when_no_image_given():
    bedrock = StubBedrockClient(['{"search_agent": true, "graph_agent": false, "lookup_agent": false, "image_agent": true}'])

    decision = runtime.decide_dispatch(bedrock, "find a moisturizer", has_image=False)

    assert decision == {"search_agent": True, "graph_agent": False, "lookup_agent": False, "image_agent": False}


def test_run_consolidation_extracts_ranked_ids():
    bedrock = StubBedrockClient(['{"ranked_product_ids": ["P2", "P1"]}'])

    ranked = runtime.run_consolidation(bedrock, "find a moisturizer", [{"product_id": "P1"}, {"product_id": "P2"}])

    assert ranked == ["P2", "P1"]


def test_invoke_runs_the_full_pipeline_to_a_verified_cited_answer(arn_env, products_table, monkeypatch):
    bedrock = StubBedrockClient(
        [
            '{"search_agent": true, "graph_agent": false, "lookup_agent": false, "image_agent": false}',  # routing
            '{"ranked_product_ids": ["P2", "P1"]}',  # consolidation
            '{"answer": "The Acme headphones have great sound.", "citations": [{"product_id": "P2", "snippet": "great sound"}]}',  # generation
            '{"verdicts": [{"product_id": "P2", "grounded": true}]}',  # verification
        ]
    )
    agentcore = StubAgentCoreClient({"arn:search": [{"product_id": "P1", "text": "ok product"}, {"product_id": "P2", "text": "great sound"}]})
    monkeypatch.setattr(runtime, "_bedrock", bedrock)
    monkeypatch.setattr(runtime, "_agentcore", agentcore)

    response = runtime.invoke({"prompt": "find a gentle moisturizer"})

    assert response["dispatched"] == ["search_agent"]
    assert agentcore.invoked_arns == ["arn:search"]
    assert response["answer"] == "The Acme headphones have great sound."
    assert response["citations"] == [
        {
            "product_id": "P2",
            "title": "Wireless Headphones",
            "image_url": "https://example.com/p2.jpg",
            "product_url": "/products/P2",
            "snippet": "great sound",
        }
    ]


def test_invoke_never_dispatches_image_agent_without_an_uploaded_image(arn_env, monkeypatch):
    bedrock = StubBedrockClient(['{"search_agent": false, "graph_agent": false, "lookup_agent": false, "image_agent": true}'])
    agentcore = StubAgentCoreClient({})
    monkeypatch.setattr(runtime, "_bedrock", bedrock)
    monkeypatch.setattr(runtime, "_agentcore", agentcore)

    response = runtime.invoke({"prompt": "what does this look like"})

    assert response["dispatched"] == []
    assert agentcore.invoked_arns == []
    assert response["citations"] == []
    assert "couldn't determine" in response["answer"]


def test_invoke_degrades_gracefully_when_one_specialist_times_out(arn_env, products_table, monkeypatch):
    """Design doc 04's "Retry/fallback": one hung specialist shouldn't fail
    the whole request - the router should still answer from whichever
    specialists did respond."""
    bedrock = StubBedrockClient(
        [
            '{"search_agent": true, "graph_agent": true, "lookup_agent": false, "image_agent": false}',  # routing
            '{"ranked_product_ids": ["P2"]}',  # consolidation
            '{"answer": "The Acme headphones have great sound.", "citations": [{"product_id": "P2", "snippet": "great sound"}]}',  # generation
            '{"verdicts": [{"product_id": "P2", "grounded": true}]}',  # verification
        ]
    )
    agentcore = StubAgentCoreClient(
        {"arn:search": [{"product_id": "P2", "text": "great sound"}]},
        timeout_arns={"arn:graph"},
    )
    monkeypatch.setattr(runtime, "_bedrock", bedrock)
    monkeypatch.setattr(runtime, "_agentcore", agentcore)

    response = runtime.invoke({"prompt": "find a gentle moisturizer"})

    assert set(agentcore.invoked_arns) == {"arn:search", "arn:graph"}
    assert response["answer"] == "The Acme headphones have great sound."
    assert response["citations"][0]["product_id"] == "P2"


def test_invoke_skips_consolidation_and_generation_when_dispatch_found_nothing(arn_env, monkeypatch):
    bedrock = StubBedrockClient(['{"search_agent": true, "graph_agent": false, "lookup_agent": false, "image_agent": false}'])
    agentcore = StubAgentCoreClient({"arn:search": []})
    monkeypatch.setattr(runtime, "_bedrock", bedrock)
    monkeypatch.setattr(runtime, "_agentcore", agentcore)

    response = runtime.invoke({"prompt": "find something extremely obscure"})

    assert response["citations"] == []
    assert bedrock.calls == 1  # only the routing call - no consolidation/generation wasted on zero candidates
    assert "None of the specialists" in response["answer"]

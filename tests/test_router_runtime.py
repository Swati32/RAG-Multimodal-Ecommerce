import base64
import json

import boto3
import pytest
from botocore.exceptions import ReadTimeoutError
from moto import mock_aws

import agents.router_runtime as runtime
from ingestion.dynamo_writer import upsert_product
from ingestion.models import Product


class StubBedrockClient:
    """Queues scripted Converse text responses (routing/consolidation/
    verification) and, separately, a queue of streamed-answer-chunk lists
    for the generation step (one entry per `invoke()` call, consumed by
    `converse_stream`) - matches the injected-stub pattern used for Claude
    calls elsewhere (see test_summarization.py)."""

    def __init__(self, texts: list[str], stream_chunks: list[str] | None = None):
        self._texts = list(texts)
        self._stream_chunks = list(stream_chunks or [])
        self.calls = 0
        self.converse_calls: list[dict] = []
        self.stream_calls: list[dict] = []

    def converse(self, **kwargs):
        self.calls += 1
        self.converse_calls.append(kwargs)
        return {"output": {"message": {"content": [{"text": self._texts.pop(0)}]}}}

    def converse_stream(self, **kwargs):
        self.calls += 1
        self.stream_calls.append(kwargs)
        return {"stream": [{"contentBlockDelta": {"delta": {"text": chunk}}} for chunk in self._stream_chunks]}


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


def _invoke(payload: dict) -> tuple[list[dict], dict]:
    """`invoke()` is a generator (see docs/designs/04-inference-serving.md,
    "streaming to client") - collect every yielded event and pull out the
    single terminal `{"type": "final", ...}` event, which carries the same
    shape the old plain-dict return used to."""
    events = list(runtime.invoke(payload))
    final = next(event for event in events if event["type"] == "final")
    return events, final


def test_decide_dispatch_forces_image_agent_off_when_no_image_given():
    bedrock = StubBedrockClient(['{"search_agent": true, "graph_agent": false, "lookup_agent": false, "image_agent": true}'])

    decision = runtime.decide_dispatch(bedrock, "find a moisturizer", has_image=False)

    assert decision == {"search_agent": True, "graph_agent": False, "lookup_agent": False, "image_agent": False}


def test_run_consolidation_extracts_ranked_ids():
    bedrock = StubBedrockClient(['{"ranked_product_ids": ["P2", "P1"]}'])

    ranked = runtime.run_consolidation(bedrock, "find a moisturizer", [{"product_id": "P1"}, {"product_id": "P2"}])

    assert ranked == ["P2", "P1"]


def test_invoke_attaches_the_uploaded_image_to_consolidation(arn_env, products_table, monkeypatch):
    """Real bug, caught live: consolidation used to be text-only even for
    an image-driven query, so Claude couldn't judge ImageAgent's candidates
    against the actual reference image and refused with prose instead of
    JSON - see docs/designs/03-image-upload.md."""
    bedrock = StubBedrockClient(
        texts=[
            '{"search_agent": false, "graph_agent": false, "lookup_agent": false, "image_agent": true}',  # routing
            '{"ranked_product_ids": ["P2"]}',  # consolidation
            '{"verdicts": [{"product_id": "P2", "grounded": true}]}',  # verification
        ],
        stream_chunks=["Similar item. [[P2]]"],
    )
    agentcore = StubAgentCoreClient({"arn:image": [{"product_id": "P2", "text": "great sound"}]})
    monkeypatch.setattr(runtime, "_bedrock", bedrock)
    monkeypatch.setattr(runtime, "_agentcore", agentcore)

    image_b64 = base64.b64encode(b"fake-jpeg-bytes").decode()

    _invoke({"prompt": "find similar products", "image_base64": image_b64})

    consolidation_call = bedrock.converse_calls[1]  # routing is [0], consolidation is [1]
    assert consolidation_call["messages"][0]["content"][0] == {"image": {"format": "jpeg", "source": {"bytes": b"fake-jpeg-bytes"}}}
    generation_call = bedrock.stream_calls[0]
    assert generation_call["messages"][0]["content"][0] == {"image": {"format": "jpeg", "source": {"bytes": b"fake-jpeg-bytes"}}}


def test_invoke_runs_the_full_pipeline_to_a_verified_cited_answer(arn_env, products_table, monkeypatch):
    bedrock = StubBedrockClient(
        texts=[
            '{"search_agent": true, "graph_agent": false, "lookup_agent": false, "image_agent": false}',  # routing
            '{"ranked_product_ids": ["P2", "P1"]}',  # consolidation
            '{"verdicts": [{"product_id": "P2", "grounded": true}]}',  # verification
        ],
        stream_chunks=["The Acme headphones have ", "great sound. [[P2]]"],  # streamed generation
    )
    agentcore = StubAgentCoreClient({"arn:search": [{"product_id": "P1", "text": "ok product"}, {"product_id": "P2", "text": "great sound"}]})
    monkeypatch.setattr(runtime, "_bedrock", bedrock)
    monkeypatch.setattr(runtime, "_agentcore", agentcore)

    events, response = _invoke({"prompt": "find a gentle moisturizer"})

    assert response["dispatched"] == ["search_agent"]
    assert agentcore.invoked_arns == ["arn:search"]
    assert response["answer"] == "The Acme headphones have great sound."
    assert response["citations"] == [
        {
            "product_id": "P2",
            "title": "Wireless Headphones",
            "image_url": "https://example.com/p2.jpg",
            "product_url": "/products/P2",
            "snippet": "The Acme headphones have great sound.",
        }
    ]
    # the answer was actually streamed, not assembled and returned in one shot
    chunk_events = [event for event in events if event["type"] == "answer_chunk"]
    assert [event["text"] for event in chunk_events] == ["The Acme headphones have ", "great sound. [[P2]]"]

    # trace exposes what each pipeline stage actually did - see
    # frontend/src/PipelineTrace.tsx, which renders this
    trace = response["trace"]
    assert trace["dispatch_decision"] == {"search_agent": True, "graph_agent": False, "lookup_agent": False, "image_agent": False}
    assert trace["specialists"] == {"search_agent": {"result_count": 2, "timed_out": False}}
    assert trace["consolidation"] == {"candidate_count": 2, "ranked_count": 2}
    assert trace["citations"] == {"drafted": 1, "verified": 1}


def test_invoke_never_dispatches_image_agent_without_an_uploaded_image(arn_env, monkeypatch):
    bedrock = StubBedrockClient(['{"search_agent": false, "graph_agent": false, "lookup_agent": false, "image_agent": true}'])
    agentcore = StubAgentCoreClient({})
    monkeypatch.setattr(runtime, "_bedrock", bedrock)
    monkeypatch.setattr(runtime, "_agentcore", agentcore)

    _, response = _invoke({"prompt": "what does this look like"})

    assert response["dispatched"] == []
    assert agentcore.invoked_arns == []
    assert response["citations"] == []
    assert "couldn't determine" in response["answer"]


def test_invoke_degrades_gracefully_when_one_specialist_times_out(arn_env, products_table, monkeypatch):
    """Design doc 04's "Retry/fallback": one hung specialist shouldn't fail
    the whole request - the router should still answer from whichever
    specialists did respond."""
    bedrock = StubBedrockClient(
        texts=[
            '{"search_agent": true, "graph_agent": true, "lookup_agent": false, "image_agent": false}',  # routing
            '{"ranked_product_ids": ["P2"]}',  # consolidation
            '{"verdicts": [{"product_id": "P2", "grounded": true}]}',  # verification
        ],
        stream_chunks=["The Acme headphones have great sound. [[P2]]"],
    )
    agentcore = StubAgentCoreClient(
        {"arn:search": [{"product_id": "P2", "text": "great sound"}]},
        timeout_arns={"arn:graph"},
    )
    monkeypatch.setattr(runtime, "_bedrock", bedrock)
    monkeypatch.setattr(runtime, "_agentcore", agentcore)

    _, response = _invoke({"prompt": "find a gentle moisturizer"})

    assert set(agentcore.invoked_arns) == {"arn:search", "arn:graph"}
    assert response["answer"] == "The Acme headphones have great sound."
    assert response["citations"][0]["product_id"] == "P2"
    assert response["trace"]["specialists"]["graph_agent"]["timed_out"] is True
    assert response["trace"]["specialists"]["search_agent"] == {"result_count": 1, "timed_out": False}


def test_invoke_skips_consolidation_and_generation_when_dispatch_found_nothing(arn_env, monkeypatch):
    bedrock = StubBedrockClient(['{"search_agent": true, "graph_agent": false, "lookup_agent": false, "image_agent": false}'])
    agentcore = StubAgentCoreClient({"arn:search": []})
    monkeypatch.setattr(runtime, "_bedrock", bedrock)
    monkeypatch.setattr(runtime, "_agentcore", agentcore)

    _, response = _invoke({"prompt": "find something extremely obscure"})

    assert response["citations"] == []
    assert bedrock.calls == 1  # only the routing call - no consolidation/generation wasted on zero candidates
    assert "None of the specialists" in response["answer"]

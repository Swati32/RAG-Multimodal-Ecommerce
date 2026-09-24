import agents.search_agent_runtime as runtime
from agents.tool_loop import run_tool_loop


class StubBedrockClient:
    """Queues scripted Converse responses AND handles embed calls (Titan's
    invoke_model), since SearchAgent's runtime uses one Bedrock client for
    both - matches the injected-stub pattern in test_summarization.py."""

    def __init__(self, converse_responses: list[dict], embedding: list[float]):
        self._converse_responses = list(converse_responses)
        self._embedding = embedding
        self.converse_calls = 0
        self.embed_calls = 0

    def converse(self, **kwargs):
        self.converse_calls += 1
        return self._converse_responses.pop(0)

    def invoke_model(self, **kwargs):
        import json

        self.embed_calls += 1

        class _Body:
            def __init__(self, payload):
                self._payload = payload

            def read(self):
                return json.dumps(self._payload).encode()

        return {"body": _Body({"embedding": self._embedding})}


class StubOpenSearchClient:
    def __init__(self, hits: list[dict]):
        self._hits = hits

    def search(self, **kwargs):
        return {"hits": {"hits": self._hits}}


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


def _hit(product_id: str) -> dict:
    return {
        "_source": {
            "chunk_id": f"{product_id}#desc0",
            "product_id": product_id,
            "category": "Electronics",
            "brand": "Acme",
            "price": 29.99,
            "avg_rating": 4.2,
            "chunk_type": "description",
            "text": "Deep bass, comfortable fit.",
        }
    }


def test_search_tool_embeds_query_and_returns_matches(monkeypatch):
    monkeypatch.setattr(runtime, "get_opensearch_client", lambda: StubOpenSearchClient([_hit("P1"), _hit("P2")]))
    bedrock = StubBedrockClient(
        [
            _tool_use_response("t1", "search_products", {"query_text": "good bass headphones", "max_price": 50}),
            _final_response("Found some options."),
        ],
        embedding=[0.1] * 1024,
    )
    monkeypatch.setattr(runtime, "_bedrock", bedrock)

    response = run_tool_loop(bedrock, runtime.MODEL_ID, runtime.INSTRUCTION, runtime.TOOLS, runtime.run_tool, "headphones with good bass under $50", runtime.MAX_TURNS)

    assert [r["product_id"] for r in response["results"]] == ["P1", "P2"]
    assert bedrock.embed_calls == 1


def test_search_tool_returns_empty_list_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(runtime, "get_opensearch_client", lambda: StubOpenSearchClient([]))
    bedrock = StubBedrockClient(
        [
            _tool_use_response("t1", "search_products", {"query_text": "left-handed scissors"}),
            _final_response("No matches found."),
        ],
        embedding=[0.1] * 1024,
    )
    monkeypatch.setattr(runtime, "_bedrock", bedrock)

    response = run_tool_loop(bedrock, runtime.MODEL_ID, runtime.INSTRUCTION, runtime.TOOLS, runtime.run_tool, "left-handed scissors", runtime.MAX_TURNS)

    assert response["results"] == []
    assert response["message"] == "No matches found."


def test_run_tool_rejects_unknown_tool_name(monkeypatch):
    monkeypatch.setattr(runtime, "get_opensearch_client", lambda: StubOpenSearchClient([]))
    assert runtime.run_tool("delete_everything", {}) == {"error": "Unknown tool: delete_everything"}

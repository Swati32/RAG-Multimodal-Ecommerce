import base64
import json

import agents.image_agent_runtime as runtime


class StubBedrockClient:
    """Handles both Converse (agent loop) and invoke_model (Cohere embed)
    calls - matches the injected-stub pattern in test_summarization.py."""

    def __init__(self, converse_responses: list[dict], embedding: list[float]):
        self._converse_responses = list(converse_responses)
        self._embedding = embedding
        self.converse_calls = 0
        self.embed_calls = 0

    def converse(self, **kwargs):
        self.converse_calls += 1
        return self._converse_responses.pop(0)

    def invoke_model(self, **kwargs):
        self.embed_calls += 1

        class _Body:
            def __init__(self, payload):
                self._payload = payload

            def read(self):
                return json.dumps(self._payload).encode()

        return {"body": _Body({"embeddings": {"float": [self._embedding]}})}


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
    return {"output": {"message": {"role": "assistant", "content": [{"text": text}]}}, "stopReason": "end_turn"}


def _hit(product_id: str) -> dict:
    return {
        "_source": {
            "image_id": f"{product_id}#image0",
            "product_id": product_id,
            "image_url": f"https://example.com/{product_id}.jpg",
            "category": "All Beauty",
            "brand": "Acme",
            "price": 19.99,
            "avg_rating": 4.3,
        }
    }


def test_invoke_embeds_the_image_and_returns_visual_matches(monkeypatch):
    monkeypatch.setattr(runtime, "get_opensearch_client", lambda: StubOpenSearchClient([_hit("P1"), _hit("P2")]))
    bedrock = StubBedrockClient(
        [
            _tool_use_response("t1", "find_visually_similar_products", {"max_price": 30}),
            _final_response("Found similar products."),
        ],
        embedding=[0.2] * 1024,
    )
    monkeypatch.setattr(runtime, "_bedrock", bedrock)

    payload = {"image_base64": base64.b64encode(b"fake-image-bytes").decode(), "prompt": "find this under $30"}
    response = runtime.invoke(payload)

    assert [r["product_id"] for r in response["results"]] == ["P1", "P2"]
    assert bedrock.embed_calls == 1


def test_invoke_uses_a_default_prompt_when_none_given(monkeypatch):
    monkeypatch.setattr(runtime, "get_opensearch_client", lambda: StubOpenSearchClient([]))
    bedrock = StubBedrockClient(
        [_tool_use_response("t1", "find_visually_similar_products", {}), _final_response("No close matches.")],
        embedding=[0.2] * 1024,
    )
    monkeypatch.setattr(runtime, "_bedrock", bedrock)

    payload = {"image_base64": base64.b64encode(b"fake-image-bytes").decode()}
    response = runtime.invoke(payload)

    assert response["results"] == []
    assert response["message"] == "No close matches."

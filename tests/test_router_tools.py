import json

import pytest

from agents.router_tools import dedupe_and_rank, dispatch_specialist, parse_json_response


def test_parse_json_response_handles_plain_json():
    assert parse_json_response('{"a": 1}') == {"a": 1}


def test_parse_json_response_strips_markdown_fence():
    assert parse_json_response('```json\n{"a": 1}\n```') == {"a": 1}


def test_dedupe_and_rank_keeps_first_seen_record_per_product_and_follows_rank_order():
    candidates = [
        {"product_id": "P1", "source": "search"},
        {"product_id": "P2", "source": "graph"},
        {"product_id": "P1", "source": "lookup"},  # duplicate of P1, seen second - dropped
    ]

    result = dedupe_and_rank(candidates, ranked_product_ids=["P2", "P1"])

    assert result == [{"product_id": "P2", "source": "graph"}, {"product_id": "P1", "source": "search"}]


def test_dedupe_and_rank_omits_ids_with_no_matching_candidate():
    candidates = [{"product_id": "P1"}]
    assert dedupe_and_rank(candidates, ranked_product_ids=["P1", "P404"]) == [{"product_id": "P1"}]


def test_dedupe_and_rank_omits_candidates_claude_left_out_of_the_ranking():
    candidates = [{"product_id": "P1"}, {"product_id": "P2"}]
    assert dedupe_and_rank(candidates, ranked_product_ids=["P2"]) == [{"product_id": "P2"}]


class _StubStreamingBody:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()


class StubAgentCoreClient:
    def __init__(self, results: list[dict]):
        self._results = results
        self.calls = []

    def invoke_agent_runtime(self, **kwargs):
        self.calls.append(kwargs)
        return {"response": _StubStreamingBody({"results": self._results, "message": "ok"})}


def test_dispatch_specialist_returns_the_results_list():
    client = StubAgentCoreClient(results=[{"product_id": "P1"}])

    results = dispatch_specialist(client, "arn:aws:...:runtime/some-agent", {"prompt": "find X"})

    assert results == [{"product_id": "P1"}]
    call = client.calls[0]
    assert call["agentRuntimeArn"] == "arn:aws:...:runtime/some-agent"
    assert json.loads(call["payload"]) == {"prompt": "find X"}
    assert len(call["runtimeSessionId"]) >= 33  # AgentCore's minimum session id length

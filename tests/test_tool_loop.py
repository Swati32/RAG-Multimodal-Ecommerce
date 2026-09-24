from botocore.exceptions import ReadTimeoutError

from agents.tool_loop import run_tool_loop


class StubBedrockClient:
    def __init__(self, responses: list[dict]):
        self._responses = list(responses)
        self.sent_messages_by_call: list[list[dict]] = []

    def converse(self, **kwargs):
        self.sent_messages_by_call.append(list(kwargs["messages"]))  # snapshot - messages list mutates in place
        response = self._responses.pop(0)
        if response is TimeoutError:
            raise ReadTimeoutError(endpoint_url="https://bedrock-runtime.us-east-2.amazonaws.com")
        return response


def _tool_use_response(tool_use_id: str, name: str, input_: dict) -> dict:
    return {
        "output": {"message": {"role": "assistant", "content": [{"toolUse": {"toolUseId": tool_use_id, "name": name, "input": input_}}]}},
        "stopReason": "tool_use",
    }


def _final_response(text: str) -> dict:
    return {"output": {"message": {"role": "assistant", "content": [{"text": text}]}}, "stopReason": "end_turn"}


def test_list_tool_result_is_wrapped_as_a_json_object_on_the_wire():
    """Converse's toolResult.content[0].json must be a JSON object, not a
    bare array - a real ValidationException otherwise (hit while building
    SearchAgent, whose tool returns list[dict])."""
    bedrock = StubBedrockClient([_tool_use_response("t1", "search", {}), _final_response("done")])
    run_tool = lambda name, input_: [{"product_id": "P1"}, {"product_id": "P2"}]  # noqa: E731

    run_tool_loop(bedrock, "model", "instruction", [], run_tool, "find things", max_turns=4)

    second_call_messages = bedrock.sent_messages_by_call[1]
    tool_result_content = second_call_messages[-1]["content"][0]["toolResult"]["content"][0]["json"]
    assert tool_result_content == {"items": [{"product_id": "P1"}, {"product_id": "P2"}]}


def test_dict_tool_result_is_sent_as_is():
    bedrock = StubBedrockClient([_tool_use_response("t1", "lookup", {}), _final_response("done")])
    run_tool = lambda name, input_: {"product_id": "P1"}  # noqa: E731

    run_tool_loop(bedrock, "model", "instruction", [], run_tool, "look up P1", max_turns=4)

    second_call_messages = bedrock.sent_messages_by_call[1]
    tool_result_content = second_call_messages[-1]["content"][0]["toolResult"]["content"][0]["json"]
    assert tool_result_content == {"product_id": "P1"}


def test_flattened_results_are_unaffected_by_wire_wrapping():
    bedrock = StubBedrockClient([_tool_use_response("t1", "search", {}), _final_response("done")])
    run_tool = lambda name, input_: [{"product_id": "P1"}, {"product_id": "P2"}]  # noqa: E731

    response = run_tool_loop(bedrock, "model", "instruction", [], run_tool, "find things", max_turns=4)

    assert response["results"] == [{"product_id": "P1"}, {"product_id": "P2"}]


def test_timeout_mid_loop_returns_partial_results_instead_of_raising():
    """Design doc 04's "Retry/fallback": on timeout, the agent proceeds
    with whatever context it already has rather than failing the request
    outright - a slow second Converse call shouldn't lose the first tool
    call's results."""
    bedrock = StubBedrockClient([_tool_use_response("t1", "search", {}), TimeoutError])
    run_tool = lambda name, input_: [{"product_id": "P1"}]  # noqa: E731

    response = run_tool_loop(bedrock, "model", "instruction", [], run_tool, "find things", max_turns=4)

    assert response["results"] == [{"product_id": "P1"}]
    assert "timed out" in response["message"]

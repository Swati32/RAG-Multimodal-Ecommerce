"""Shared Claude tool-use loop for retrieval specialist agents (LookupAgent,
SearchAgent, ...) - see docs/designs/02-retrieval-agents.md, "Agent
implementation".

Every specialist returns *structured* results to the router, not a prose
answer - the router fuses results from every dispatched specialist and a
separate generator call turns that into the user-facing answer (see the
design doc's sequence diagram: "SRCH-->>RTR: results", not "SRCH-->>RTR:
answer"). So this loop executes tool calls and collects their raw
structured output across turns, rather than asking Claude to paraphrase a
final answer - a bounded number of turns is still allowed so an agent can
reformulate and retry (e.g. broaden a search that returned too little)
before giving up, per the design doc's reasoning for using an agent loop
at all.
"""

from __future__ import annotations

from typing import Callable

Tool = dict
RunTool = Callable[[str, dict], dict]


def run_tool_loop(
    bedrock,
    model_id: str,
    instruction: str,
    tools: list[Tool],
    run_tool: RunTool,
    user_message: str | list[dict],
    max_turns: int,
) -> dict:
    """Returns {"results": [...structured tool outputs...], "message": str|None}.
    `message` is only set when Claude responded with text instead of (or
    after) calling tools - e.g. declining an out-of-scope request, or
    explaining why nothing was found.

    `run_tool` may return a single record (dict, e.g. LookupAgent) or
    multiple (list[dict], e.g. SearchAgent) - both flatten into the same
    `results` list rather than nesting a list-within-a-list.

    `user_message` is plain text for text-only agents, or a pre-built
    Converse content list (e.g. `[{"image": {...}}, {"text": ...}]`) for a
    multimodal agent like ImageAgent - Claude sees the image directly, but
    `run_tool` only ever receives what Claude explicitly puts in a tool
    call's arguments, never raw image bytes echoed back through them."""
    content = [{"text": user_message}] if isinstance(user_message, str) else user_message
    messages = [{"role": "user", "content": content}]
    results: list[dict] = []

    for _ in range(max_turns):
        response = bedrock.converse(
            modelId=model_id,
            system=[{"text": instruction}],
            messages=messages,
            toolConfig={"tools": tools},
        )
        output_message = response["output"]["message"]
        messages.append(output_message)

        tool_uses = [block["toolUse"] for block in output_message["content"] if "toolUse" in block]
        if not tool_uses:
            text = next((block["text"] for block in output_message["content"] if "text" in block), None)
            return {"results": results, "message": text}

        tool_results = []
        for tool_use in tool_uses:
            result = run_tool(tool_use["name"], tool_use["input"])
            results.extend(result if isinstance(result, list) else [result])
            # Converse's toolResult content.json must be a JSON object, not
            # a bare array (a real ValidationException otherwise) - wrap a
            # list result so it round-trips back to Claude correctly.
            wire_result = {"items": result} if isinstance(result, list) else result
            tool_results.append({"toolResult": {"toolUseId": tool_use["toolUseId"], "content": [{"json": wire_result}]}})
        messages.append({"role": "user", "content": tool_results})

    return {"results": results, "message": "Reached the turn limit before finishing."}

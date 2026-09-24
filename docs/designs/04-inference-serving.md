# 04 — Inference Serving

## Overview

Distinct from [02 — Retrieval Agents](02-retrieval-agents.md), which decides *what* to retrieve — this covers *how* each model call is served: which mode, what latency to expect, and how failures are handled.

**Addresses**: NFR-2 (latency is a soft target only, not a hard constraint).

## Model calls by stage

| Stage | Model | Mode | Notes |
| --- | --- | --- | --- |
| Query embedding | Titan Text/Multimodal Embeddings | Real-time InvokeModel | Single item, low latency |
| Bulk embedding | Titan Text/Multimodal Embeddings | Bedrock Batch | High throughput, no latency requirement |
| Agent tool-calling steps | Claude (via Bedrock) | Real-time, non-streaming | Needs the full tool-call decision before acting; streaming doesn't help here |
| Final answer generation | Claude (via Bedrock) | Real-time, streaming to client | Streaming improves perceived latency for a chat-style answer |

## Decisions & reasoning

**Retry/fallback**: exponential backoff with jitter on Bedrock throttling errors; a hard timeout per agent step (e.g. 10s) so one slow tool call can't hang the whole request — on timeout, the agent proceeds with whatever context it already has rather than failing the request outright.

**Latency budget** (soft target for demo usability, not a hard requirement — see [Non-Functional Requirements](../../README.md)): query embedding ~100ms, each retrieval agent call ~200–400ms, up to 2 router iterations, final generation streaming starts within ~1.5s of fused context being ready.

**Prompt/version management**: pin a specific Bedrock model version (not "latest") for both the router and the generator so behavior doesn't shift between runs; track prompt templates in version control alongside the ingestion code.

## Implementation notes

**Retry/fallback (done for real, deployed and verified)** — see [src/agents/bedrock_client.py](../../src/agents/bedrock_client.py):
- Both client types (`bedrock-runtime` for Converse calls, `bedrock-agentcore` for specialist dispatch) are built through two shared factory functions instead of each agent runtime hand-rolling its own `boto3.client(...)` call, so every agent gets identical retry/timeout behavior.
- **Retry**: `Config(retries={"mode": "standard", "max_attempts": 3})`. Boto3's `standard` retry mode already implements exponential backoff with jitter for retryable errors, including Bedrock's `ThrottlingException` — no need to hand-roll a backoff loop. `adaptive` mode was considered and rejected: it adds client-side rate limiting (a token bucket) built for sustained bulk throughput, which the ingestion pipeline's own `RateLimiter` (`src/rate_limiter.py`) already handles for its bulk embedding jobs — these are low-volume, real-time, single-call agent steps, a different traffic shape that doesn't need it.
- **Timeout**: `bedrock_runtime_client` gets a 10s `read_timeout` (matching this doc's own "e.g. 10s" example) — one Converse call is one agent step. `agentcore_client` gets a 20s `read_timeout`, since one specialist dispatch is a full multi-turn tool-use loop internally (`MAX_TURNS = 4` in every `*_agent_runtime.py`), not a single model call, so it needs more headroom than one bare Converse call.
- **Graceful degradation on timeout**: a timeout mid-loop in `tool_loop.py` (a specialist's own Converse call) returns whatever `results` were already collected from completed tool calls, instead of raising and failing the whole specialist request. A timeout in the router's specialist dispatch (`router_runtime.py`) is caught per-specialist inside the `ThreadPoolExecutor` result-collection loop — one hung specialist is skipped (logged, not raised) and the router still answers from whichever specialists did respond. Both paths are unit-tested with a stub client that raises the real `botocore.exceptions.ReadTimeoutError` mid-sequence (`tests/test_tool_loop.py::test_timeout_mid_loop_returns_partial_results_instead_of_raising`, `tests/test_router_runtime.py::test_invoke_degrades_gracefully_when_one_specialist_times_out`) — forcing a genuine Bedrock-side timeout on demand isn't practical, so the timeout path itself is tested against the real exception type rather than live-triggered; the surrounding client config change was verified live by redeploying `RagEcommerce-Agents` and re-running a real router invocation end-to-end with no regression.

**Streaming final-answer generation (done for real, deployed and verified)** — a deliberate deviation from design doc 02's original Answer Format, flagged here per the Implementation Fidelity rule in CLAUDE.md:
- **Why the format had to change**: the old generator asked Claude for one JSON blob (`{"answer": ..., "citations": [...]}`). JSON doesn't stream usefully — a client can't render a half-formed object, so streaming it would only improve time-to-first-byte, not perceived latency of the actual content. Fixed by having the generator write the answer as plain text with inline citation markers (`[[product_id]]` right after a cited claim) instead — see the reworked `GENERATOR_INSTRUCTION` in [src/agents/answer_generation.py](../../src/agents/answer_generation.py).
- **`stream_answer`** calls Bedrock's `converse_stream` and yields text deltas as they arrive. **`extract_citations`** parses `[[product_id]]` markers out of the accumulated full answer text once streaming finishes (not per-chunk — a marker can and does split across chunk boundaries in practice, e.g. a real streamed response split `[[B07968N5GC]]` into `"[["` and `"B07968N5GC]]"` as separate chunks; running the regex against the reassembled string sidesteps that entirely) and turns each into a `{"product_id", "snippet"}` citation, where the snippet is the sentence (or paragraph) immediately before its marker — the streamed answer text itself is now the source of the citation, not a separately requested JSON field. `verify_citations`/`resolve_citations` are unchanged downstream.
- **The AgentCore entrypoint (`invoke` in `router_runtime.py`) is now a generator**, not a plain function — `bedrock_agentcore`'s `BedrockAgentCoreApp` auto-detects a generator return value and streams it back to the caller as real server-sent events (confirmed by reading the installed SDK, `bedrock_agentcore/runtime/app.py`: `if inspect.isgenerator(result): return StreamingResponse(...)`). Every code path yields exactly one terminal `{"type": "final", "answer": ..., "citations": ..., "dispatched": ...}` event — the same shape the old plain-dict return used to have — plus, only on the path that actually calls the generator, a `{"type": "answer_chunk", "text": ...}` event per streamed delta.
- **Real IAM gap found and fixed**: `converse_stream` calls `bedrock:InvokeModelWithResponseStream`, a separate IAM action from plain `bedrock:InvokeModel` — not implied by it. A real deploy-time `AccessDeniedException` on the streaming action (even though `InvokeModel` already worked) confirmed this; fixed by adding the action to the shared policy statement in `agents_stack.py`'s `_build_runtime`, so every agent runtime gets it, not just the router.
- **Verified live**: invoking the deployed router with `aws bedrock-agentcore invoke-agent-runtime` returned `contentType: text/event-stream; charset=utf-8` and a real sequence of `data: {...}\n\n` SSE events — dozens of small `answer_chunk` deltas (including the cross-chunk marker split above) followed by one `final` event with clean, correctly-deduplicated citations. Unit-tested too: `tests/test_answer_generation.py` (stream/marker-parsing in isolation) and `tests/test_router_runtime.py` (the full generator contract, collecting all yielded events and checking both the chunk sequence and the terminal event).

## Status

Retry/timeout/graceful-degradation: **Complete.** Streaming final-answer generation: **Complete.** See [../PROGRESS.md](../PROGRESS.md).

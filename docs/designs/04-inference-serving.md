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

**Streaming final-answer generation**: not yet built. The current generator (`generate_answer` in `src/agents/answer_generation.py`) asks Claude for one JSON blob (`{"answer": ..., "citations": [...]}`) per this doc's original "Answer format" design, which doesn't stream cleanly — streaming raw JSON tokens to a client is awkward to consume before the object is complete. `bedrock_agentcore`'s `BedrockAgentCoreApp` does support a streaming entrypoint (a sync or async generator return value is auto-wrapped as an SSE `StreamingResponse` — confirmed by reading the installed SDK, `bedrock_agentcore/runtime/app.py`), so it's feasible, but it needs a real answer-format decision first (e.g. stream free-text with inline citation markers, resolve citations as a fast non-streaming follow-up) — deferred rather than implemented as a silent format change, since it touches the already-shipped Answer Format from [02](02-retrieval-agents.md#answer-format). Tracked as a follow-up in [PROGRESS.md](../PROGRESS.md).

## Status

Retry/timeout/graceful-degradation: **Complete.** Streaming: not started. See [../PROGRESS.md](../PROGRESS.md).

# Experiment 02 — Summarization Prompting Strategy

## Question

`src/ingestion/summarization.py` summarizes a long review with a single fixed prompt before it's read by [Experiment 01](01-chunking-strategy.md)'s chunker (design doc [02](../designs/02-retrieval-agents.md), "Agents vs Direct LLM Calls"). Is that prompt actually the best choice, or does a different framing produce better summaries?

## Method (designed, not yet run — see Status)

Three candidate prompts, defined in [`scripts/experiments/prompt_candidates.py`](../../scripts/experiments/prompt_candidates.py):

- **`concise_claims`** (current default) — "summarize in under 100 words, keep every concrete claim"
- **`bullet_extraction`** — extract concrete claims as bullet points, omit generic sentiment
- **`aggressive_compression`** — summarize in under 50 words, prioritize the single most useful claim

Evaluation: **LLM-as-judge**, per the technique chosen in design doc [05](../designs/05-observability-cost.md). For each candidate prompt, summarize 10 real long reviews (≥150 words, streamed from the dataset), then have a separate, stronger model score each (review, summary) pair on:
- **Groundedness (1–5)** — does the summary avoid inventing claims not in the original
- **Coverage (1–5)** — does it retain the key concrete claims

Generator model: Claude Haiku (cheap/fast — matches the bulk, ingestion-time use case). Judge model: a stronger Claude model, deliberately different from the generator to avoid self-grading bias.

Script: [`scripts/experiments/run_prompting_experiment.py`](../../scripts/experiments/run_prompting_experiment.py).

## Status: blocked on Bedrock model access, not yet run

Attempting to run this surfaced two real, sequential AWS Bedrock account-setup steps on account 953146692069:

1. **Account verification hold** (`AccessDeniedException: Your account is currently being verified`) — cleared on its own within about an hour.
2. **Anthropic model use-case form** (`ResourceNotFoundException: Model use case details have not been submitted for this account`) — a one-time form in the Bedrock console (Model access page) required before invoking Anthropic models. Still blocking as of 2026-09-18. This is a business-facing form, not something to submit on the account owner's behalf.

Also discovered along the way: these newer Claude models require **inference profile IDs** for on-demand invocation, not bare model IDs — e.g. `us.anthropic.claude-haiku-4-5-20251001-v1:0`, not `anthropic.claude-haiku-4-5-20251001-v1:0` (`ValidationException` otherwise). And not every model listed by `aws bedrock list-foundation-models` is actually enabled for the account — `us.anthropic.claude-sonnet-5` specifically returned `AccessDeniedException: ... is not available for this account` even after the verification hold cleared; the judge model was switched to `us.anthropic.claude-sonnet-4-5-20250929-v1:0`, which passed that check.

**To run once unblocked**:
```bash
cd scripts/experiments && python run_prompting_experiment.py
```

No decision has been made yet — the current default prompt (`concise_claims`) stays in `src/ingestion/summarization.py` until this experiment actually runs.

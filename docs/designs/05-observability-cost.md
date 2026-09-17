# 05 — Observability & Cost Controls

## Overview

What to monitor across the stack, the failure modes worth watching for, and how the design stays under a $100/month AWS spend cap.

**Addresses**: NFR-1 (cost), NFR-5 (observability).

## Cost budget

**Target: under $100/month total AWS spend.**

**The trap to avoid**: OpenSearch Serverless and Neptune Serverless both advertise "scale to zero" but carry minimum capacity floors that bill even while idle — OpenSearch Serverless's minimum (2 OCU indexing + 2 OCU search) alone can run several hundred dollars a month, and Neptune Serverless's minimum NCU floor isn't free either. Neither is safe under this cap. **Provisioned, not Serverless, for both — smallest instance size available.**

Latency isn't a constraint for this project (see Non-Functional Requirements), so there's no performance tradeoff being traded away by choosing the cheapest instance/capacity size anywhere — it's a straightforward cost win.

| Service | Choice | Why |
| --- | --- | --- |
| OpenSearch | Single-node provisioned domain, smallest instance size, no dedicated master | Cheapest provisioned option; still bills 24/7 while it exists |
| Neptune | Smallest provisioned instance, or replace with a DynamoDB adjacency-list graph (`PK=node`, `SK=edge_type#target`) | The DynamoDB swap removes this cost entirely (pay-per-request) at the cost of native Gremlin/openCypher traversal — acceptable since the graph queries needed (category hierarchy, co-purchase, brand) are 1–2 hop lookups |
| DynamoDB, S3, Lambda, API Gateway | Pay-per-use as designed | Negligible cost at this scale, no idle risk |
| Bedrock | Batch for bulk ingestion, capped max-output-tokens per query | Batch is cheaper per-token; the output cap bounds worst-case cost per query |
| Dataset | 5,000 products (confirmed, halved from an original 5–10k estimate) | Bounds index/storage size regardless of engine choice |

**Discipline, not just sizing**: the biggest lever is not leaving OpenSearch/Neptune running between work sessions — tear them down via IaC and recreate before each session rather than paying 24/7 for infrastructure used a few hours a week.

**Guardrails**: AWS Budgets with two SNS-notified thresholds, an $80 warning and a $100 alert; tag every resource with a project tag so Cost Explorer can isolate this project's spend.

**Open question**: does the demo need to be always-live (e.g. for an interview walkthrough), or can it be brought up on demand? That decides whether provisioned-and-idle OpenSearch/Neptune is acceptable, or whether the DynamoDB-graph swap is worth doing up front.

## Metrics to monitor

**Infrastructure (CloudWatch)**

| Service | Watch |
| --- | --- |
| DynamoDB | Throttled requests, consumed read/write capacity |
| OpenSearch | Cluster health, query latency p50/p95, JVM memory pressure |
| Neptune | Query latency, Gremlin/openCypher error rate |
| Bedrock | Invocation latency, throttling errors, token usage (cost driver) |
| Step Functions | Failed executions per ingestion run |
| Lambda (API + agents) | Error rate, cold start duration, concurrent executions |

**Application/quality**

- Retrieval quality against a small hand-labeled eval set (precision@k)
- Citation grounding rate: fraction of answers whose claims trace back to a retrieved chunk
- End-to-end answer latency (p50/p95) and cost per query
- Agent iteration count per query (a rising average suggests retrieval isn't converging)

## Pitfalls to watch for

- **Embedding drift**: re-embedding with a newer model without reindexing everything leaves two incompatible vector spaces in the same index — always full-reindex on model change
- **Stale graph edges**: co-purchase/category edges built once at ingestion go stale as the catalog changes — needs the refresh cadence in [01](01-ingestion-pipeline.md), not a one-time load
- **Hallucinated confidence despite citations**: the model can cite a real product for an unsupported claim about it — spot-check citation *accuracy*, not just citation *presence* (see the verifier step in [02](02-retrieval-agents.md))
- **Cold starts**: Lambda cold starts on the agent path add latency spikes; consider provisioned concurrency if p95 matters for a demo
- **Throttling under load**: OpenSearch and Bedrock both throttle under burst traffic — exercise backoff logic (see [04](04-inference-serving.md)) in testing, not just in code
- **Batch job partial failures**: a Bedrock Batch or Glue job failing partway through must be safely re-runnable (ties to idempotency in [01](01-ingestion-pipeline.md))
- **Cost creep**: OpenSearch and Neptune bill for provisioned capacity even when idle, and their Serverless variants have high minimum floors, not zero — smallest provisioned size plus deliberate teardown between sessions is the actual lever

## Evaluation framework

**What's evaluated**: retrieval quality, answer groundedness, and comparisons across candidate configurations — different LLMs for generation, different embedding models, single-agent vs multi-agent retrieval (see [02](02-retrieval-agents.md)), different chunking strategies — so architecture decisions are backed by a number, not just a design-doc argument.

**Deterministic metrics** (cheap, no LLM call): precision@k / recall@k against a hand-labeled eval set (query → expected `product_id`s), citation presence, latency, cost per query.

**LLM-as-judge** (for what exact-match can't cover, like "did the model make something up"): a separate Claude call — ideally a different/stronger model than the one being evaluated, to avoid self-grading bias — scores each (question, answer, retrieved context) triple against a rubric: groundedness (1–5), relevance (1–5), and per-claim citation accuracy (does the citation actually support the claim, not just exist).

**Held-out eval set**: roughly 50–100 hand-labeled query/expected-product pairs across representative categories, kept separate from anything used in prompt or config tuning, so evaluation doesn't just reward overfitting to the eval set itself.

**Comparison harness**: the eval set runs through each candidate configuration as a batch job, producing one score per metric per configuration — this is how "is multi-agent retrieval actually better than single-agent here" gets answered empirically rather than argued.

**CloudWatch integration**: each eval run publishes custom metrics via `PutMetricData` under a dedicated namespace (`RAGEcommerce/Eval`), with dimensions for `Model`, `AgentConfig`, and `EmbeddingModel` so scores are comparable across configurations and over time, alongside the infrastructure metrics above:

| Metric | What it tracks |
| --- | --- |
| `RetrievalPrecisionAtK` | Deterministic retrieval quality |
| `CitationGroundingRate` | Fraction of claims traceable to a retrieved chunk |
| `JudgeGroundednessScore` | LLM-judge groundedness rating |
| `JudgeRelevanceScore` | LLM-judge relevance rating |
| `AnswerLatencyMs` | End-to-end latency for the eval run |
| `CostPerQueryUsd` | Bedrock + retrieval cost per query |

A CloudWatch alarm on a sustained drop in `JudgeGroundednessScore` or `CitationGroundingRate` flags a regression (e.g. after a prompt or model change) the same way an infra alarm flags a latency spike.

**When it runs**: manually triggered after any change to prompts, model versions, retrieval configuration, or chunking strategy — not continuously. A portfolio project doesn't need a live CI eval gate, and each run costs Bedrock tokens.

## Status

Not started — see [../PROGRESS.md](../PROGRESS.md)

# 05 — Observability & Cost Controls

## Overview

What to monitor across the stack, the failure modes worth watching for, and how the design stays under a $100/month AWS spend cap.

**Addresses**: NFR-1 (cost), NFR-5 (observability).

## Cost budget

**Target: under $100/month total AWS spend.**

**The trap to avoid**: OpenSearch Serverless advertises "scale to zero" but carries a minimum capacity floor (2 OCU indexing + 2 OCU search) that bills even while idle — several hundred dollars a month on its own. Not safe under this cap. **Provisioned, not Serverless — smallest instance size available.**

Latency isn't a constraint for this project (see Non-Functional Requirements), so there's no performance tradeoff being traded away by choosing the cheapest instance/capacity size anywhere — it's a straightforward cost win.

| Service | Choice | Why |
| --- | --- | --- |
| OpenSearch | Single-node provisioned domain, smallest instance size, no dedicated master | Cheapest provisioned option; still bills 24/7 while it exists |
| Knowledge graph | DynamoDB adjacency-list (`PK=node`, `SK=edge_type#target`) — no Neptune | Not just cheaper: a real deploy attempt showed this AWS account's plan doesn't support Neptune at all (`CREATE_FAILED`, only `aurora-postgresql` available). The graph queries needed (category hierarchy, co-purchase, brand) are 1–2 hop lookups DynamoDB handles fine — see [01](01-ingestion-pipeline.md#graph-storage-dynamodb-not-neptune) |
| DynamoDB, S3, Lambda, API Gateway | Pay-per-use as designed | Negligible cost at this scale, no idle risk |
| Bedrock | Batch for bulk ingestion, capped max-output-tokens per query | Batch is cheaper per-token; the output cap bounds worst-case cost per query |
| Dataset | 5,000 products (confirmed, halved from an original 5–10k estimate) | Bounds index/storage size regardless of engine choice |

**Discipline, not just sizing**: the biggest lever is not leaving OpenSearch running between work sessions — tear it down via IaC and recreate before each session rather than paying 24/7 for infrastructure used a few hours a week.

**Guardrails**: AWS Budgets with two SNS-notified thresholds, an $80 warning and a $100 alert; tag every resource with a project tag so Cost Explorer can isolate this project's spend.

## Metrics to monitor

**Infrastructure (CloudWatch)**

| Service | Watch |
| --- | --- |
| DynamoDB | Throttled requests, consumed read/write capacity (including the `GraphEdges` table) |
| OpenSearch | Cluster health, query latency p50/p95, JVM memory pressure |
| Bedrock | Invocation latency, throttling errors, token usage (cost driver) |
| Step Functions | Failed executions per ingestion run |
| Lambda (API + agents) | Error rate, cold start duration, concurrent executions |

**Application/quality**

- Retrieval quality against a small hand-labeled eval set: **precision@k** — of the top-k results returned for a query, the fraction that are actually relevant (`relevant items in top k / k`)
- **Citation grounding rate**: of the claims in a generated answer, the fraction actually supported by the retrieved chunks (`grounded claims / total claims`) — catches a model citing a real product for an unsupported claim about it, which citation *presence* (did it cite something at all) can't
- End-to-end answer latency (p50/p95) and cost per query
- Agent iteration count per query (a rising average suggests retrieval isn't converging)

## Pitfalls to watch for

- **Embedding drift**: re-embedding with a newer model without reindexing everything leaves two incompatible vector spaces in the same index — always full-reindex on model change
- **Stale graph edges**: co-purchase/category edges built once at ingestion go stale as the catalog changes — needs the refresh cadence in [01](01-ingestion-pipeline.md), not a one-time load
- **Hallucinated confidence despite citations**: the model can cite a real product for an unsupported claim about it — spot-check citation *accuracy*, not just citation *presence* (see the verifier step in [02](02-retrieval-agents.md))
- **Cold starts**: Lambda cold starts on the agent path add latency spikes; consider provisioned concurrency if p95 matters for a demo
- **Throttling under load**: OpenSearch and Bedrock both throttle under burst traffic — exercise backoff logic (see [04](04-inference-serving.md)) in testing, not just in code
- **Batch job partial failures**: a Bedrock Batch or Glue job failing partway through must be safely re-runnable (ties to idempotency in [01](01-ingestion-pipeline.md))
- **Cost creep**: OpenSearch bills for provisioned capacity even when idle, and its Serverless variant has a high minimum floor, not zero — smallest provisioned size plus deliberate teardown between sessions is the actual lever

## Evaluation framework

**What's evaluated**: retrieval quality, answer groundedness, and comparisons across candidate configurations — different LLMs for generation, different embedding models, single-agent vs multi-agent retrieval (see [02](02-retrieval-agents.md)), different chunking strategies — so architecture decisions are backed by a number, not just a design-doc argument.

**Deterministic metrics** (cheap, no LLM call): precision@k (see above) and **recall@k** — of all the relevant results that exist, the fraction actually retrieved in the top-k (`relevant items retrieved / total relevant items`) — against a hand-labeled eval set (query → expected `product_id`s), plus citation presence, latency, cost per query.

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

## Implementation notes

**Done for real, deployed and verified live** — `RagEcommerce-Observability` (`infra/stacks/observability_stack.py`):

**Cost guardrails**: a real `AWS::Budgets::Budget` ($100/month, `ACTUAL` spend) with two SNS-notified thresholds (80%, 100%) exactly as designed, an SNS topic (`rag-ecommerce-alerts`) with an email subscription and the resource policy `budgets.amazonaws.com` needs to actually publish to it, and `Project: rag-ecommerce` tagged onto every resource in the app (`cdk.Tags.of(app).add(...)` in `infra/app.py`) for Cost Explorer isolation. Verified live via `aws budgets describe-budgets` (real $100 budget present) and `aws sns list-subscriptions-by-topic` (real pending email confirmation — AWS emails a confirm link to the subscribed address; alerts won't deliver until it's clicked).

**Infrastructure metrics**: a real CloudWatch dashboard (`rag-ecommerce`) built from the actual deployed resources — DynamoDB consumed capacity/throttles for all three tables, OpenSearch cluster health/search latency, Bedrock invocations/throttles/latency, Step Functions execution success/failure for the refresh pipeline, and the two API Lambdas' errors/duration — plus a widget for the eval metrics below. Referenced by resource *name*, not by importing other stacks' CDK objects, so this stack has no hard deploy-ordering dependency on them (a metric with no data yet just shows empty, not a CFN error). Spot-verified live with `get-metric-statistics` against real Lambda invocation counts and DynamoDB capacity from this session's own testing.

**Alarms**: three real `AWS::CloudWatch::Alarm` resources wired to the same SNS topic — refresh-pipeline failed executions, each API Lambda's error count, and the Reviews table's throttled requests (the table under the most write load). A real bug caught while building these: DynamoDB table names are unresolved CDK tokens at synth time, not literal strings — a `"Reviews" in name` substring check to auto-detect which table was "the Reviews one" silently matched nothing and fell back to the wrong table (`cdk synth` showed the alarm's `Dimensions` pointing at `ProductsTable`'s export). Fixed by passing `reviews_table_name` explicitly instead of guessing from a list.

**Evaluation framework**: `scripts/eval/run_production_eval.py` + `eval_utils.py` — a real harness against the *deployed* system (not a throwaway index like the earlier retrieval-only experiments), publishing real custom metrics to CloudWatch under `RAGEcommerce/Eval` with the `Model`/`AgentConfig`/`EmbeddingModel` dimensions the design calls for. Run for real (20 queries) — see [Experiment 06](experiments/06-production-eval.md) for the full method, metric definitions, and results, including a genuine finding the harness surfaced (citation snippets are self-referential post-workflow-04) that no individual workflow's own verification step had caught. Scoped down from the design's 50–100 hand-labeled pairs to 20 synthetically-labeled ones — flagged explicitly in the experiment doc, not silently substituted.

## Status

**Complete** (cost guardrails, dashboard, alarms, and a real — if smaller-than-spec — eval harness all deployed and run for real). See [../PROGRESS.md](../PROGRESS.md) and [Experiment 06](experiments/06-production-eval.md).

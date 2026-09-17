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

## Status

Not started — see [../PROGRESS.md](../PROGRESS.md)

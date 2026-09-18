# 01 — Ingestion & Refresh Pipeline

## Overview

Loads the Amazon Reviews 2023 dataset (5,000 products, plus their reviews and images) from S3 into DynamoDB and OpenSearch, and supports periodic incremental refreshes on top of the initial load — not a single one-time import.

**Addresses**: FR4 (periodic data refresh), NFR-3 (scale: 5,000-product cap), NFR-4 (reliability/idempotency), NFR-1 (cost).

## Data flow

```mermaid
%%{init: {'flowchart': {'curve': 'linear'}}}%%
flowchart TD
    A[S3 raw zone: reviews.jsonl, metadata.jsonl, images] --> B[Glue job: normalize + chunk]
    B --> C[(DynamoDB: Products, Reviews)]
    B --> D[S3 processed zone: text chunks + image refs]
    D --> E[Bedrock Batch: embed text + images]
    E --> F[S3: embeddings output]
    F --> G[Loader job: bulk index]
    G --> H[(OpenSearch: hybrid index)]
    B --> I[Graph builder job]
    I --> J[(DynamoDB: graph edges)]
```

**Steps**:
1. Download the dataset into the S3 raw zone
2. Glue job normalizes raw records into product/review rows, writes them to DynamoDB, and produces chunked text ready for embedding in the S3 processed zone
3. Bedrock Batch job embeds all text chunks and images, writing vectors back to S3
4. Loader job bulk-indexes chunks + vectors + metadata into OpenSearch
5. Graph builder job derives edges from category hierarchy and "also bought / also viewed" fields, writes them into the DynamoDB graph-edges table

A Step Functions state machine sequences steps 2–5.

## Decisions & reasoning

### Chunking strategy

**Resolved by experiment**: sentence-aware splitting (pack whole sentences into a ~300-word budget, never cut mid-sentence, no overlap needed) — not fixed-size word-count splitting with overlap, which was the original spec here. See [Experiment 01](../experiments/01-chunking-strategy.md) for the comparison and why; implemented in `src/ingestion/chunking.py`.

| Content | Approach | Why |
| --- | --- | --- |
| Product descriptions | Embed whole if under ~300 words; otherwise sentence-aware split into ~300-word chunks | Most descriptions are short enough to need no splitting at all |
| Reviews | One chunk per review in most cases; long reviews sentence-aware split at the same budget | Reviews are typically short; splitting only when needed keeps chunk count down |
| Tags/attributes (brand, category, color, size) | Not chunked as free text — stored as structured metadata on every chunk (see OpenSearch filtering below); additionally rendered as one synthetic sentence per product (e.g. "Black leather wireless over-ear headphones by Sony") and embedded | Lets attribute-phrased semantic queries match, without treating structured facts as prose |
| Images | One embedding per image, no chunking; multiple images per product are separate documents linked by `product_id` | Images aren't decomposable the way text is |

Every chunk gets a deterministic id (`{product_id}#{chunk_index}`) carrying `product_id` as metadata, so any retrieved chunk traces back to its source product (and review id, for reviews).

### Embedding generation: Bedrock Batch for bulk, real-time for query

Bulk ingestion embeds hundreds to thousands of items with no latency requirement — Bedrock Batch is roughly half the cost of on-demand invocation and built for exactly this (submit a manifest in S3, get vectors back in S3, no throttling from a tight real-time loop). Query-time embedding of a single user query needs milliseconds, so it goes through the real-time endpoint instead — batch jobs are minutes-to-hours turnaround, unsuitable for an interactive request.

If the embedding model changes, re-run the batch job over the full corpus rather than patching individual vectors — this avoids two incompatible embedding spaces coexisting in the same index.

### Graph storage: DynamoDB, not Neptune

The design originally specced Amazon Neptune for the knowledge graph, with a DynamoDB adjacency-list as a cost-saving fallback (see [05](05-observability-cost.md)). A real deploy attempt settled it: this AWS account is on a plan that doesn't support Neptune at all — `CREATE_FAILED` on `AWS::Neptune::DBCluster` with *"The specified cluster engine type is not available with free plan accounts. Available engine types: [aurora-postgresql]"*. Aurora PostgreSQL (with an extension like Apache AGE) was the available alternative, but that's a new stateful service with its own always-on cost for graph queries that are just 1–2 hop lookups — disproportionate to the need.

**Decision**: DynamoDB adjacency-list is now the only graph implementation, not a fallback. One table, `GraphEdges`:
- `node` (partition key) — `"product#P1"`, `"category#Electronics"`, `"brand#Acme"`
- `edge` (sort key) — `"{edge_type}#{target}"`, e.g. `"BELONGS_TO#category#Electronics"`, `"CO_PURCHASED_WITH#product#P2"`

Symmetric relationships (co-purchase) are written in both directions at ingestion time, so every traversal GraphAgent needs — "everything connected to this node" — is a single-partition `Query`, no GSI and no second index to keep in sync.

### Simulating refresh cadence

Rather than one bulk load, the dataset is split so the pipeline runs as a recurring process, the way it would in production:

- **Initial load**: full catalog + all reviews up to a cutoff date (the dataset carries real review timestamps, so this is a natural date split)
- **Delta batches**: reviews partitioned into monthly/weekly slices from the cutoff onward, each fed through the same pipeline as its own run

Each delta run exercises the pipeline as *updates*, not just inserts:
- DynamoDB: upsert by `product_id`
- OpenSearch: document upsert by chunk id — only the delta's chunks go through Bedrock Batch re-embedding, keeping re-embedding cost proportional to what changed
- DynamoDB graph edges: conditional writes (put only if the exact edge doesn't already exist), so replaying a delta batch never duplicates edges

**Trigger**: an EventBridge scheduled rule running once daily, invoking the Step Functions execution for the next delta partition — a live cadence rather than a manual one, so the system continuously demonstrates ingesting updates without someone kicking off each run.

This also gives a concrete staleness test: run two delta batches with a co-purchase signal that changes between them, and confirm the graph and search index both reflect the newer state.

### Idempotency

The dataset's ASIN is used as `product_id` everywhere (DynamoDB key, OpenSearch doc id, the `node`/`target` fields in `GraphEdges`), so re-running any stage overwrites rather than duplicates.

## Open questions

- Resolved: AWS CDK (Python) is the IaC tool — see [../PROGRESS.md](../PROGRESS.md#infrastructure-infra); the `RagEcommerce-Data` (S3, DynamoDB) and `RagEcommerce-Search` (OpenSearch) stacks are written and synthesize cleanly, not yet deployed
- Resolved: no Neptune stack — this account's plan doesn't support it; see "Graph storage: DynamoDB, not Neptune" above

## Status

Not started — see [../PROGRESS.md](../PROGRESS.md)

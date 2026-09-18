# RAG Multimodal E-commerce

A multimodal RAG assistant over e-commerce product data, built entirely on AWS-native services. It answers natural-language questions about products — and questions posed as an uploaded photo — by combining structured filtering, hybrid text+vector search, and knowledge-graph traversal, then generates a cited answer instead of free-floating prose.

**Dataset**: [McAuley Lab "Amazon Reviews 2023"](https://amazon-reviews-2023.github.io/) — product metadata, review text, and product images (5,000-product subset).

## Architecture

```mermaid
%%{init: {'flowchart': {'curve': 'linear'}}}%%
flowchart TB
    subgraph Client
        FE[Frontend: S3 + CloudFront SPA]
    end

    subgraph API Layer
        APIGW[API Gateway]
        LAMBDA[Lambda: request handler]
    end

    subgraph Retrieval Agents
        RTR[Router agent]
        SRCH[SearchAgent]
        IMG[ImageAgent]
        GRF[GraphAgent]
        LKP[LookupAgent]
    end

    subgraph Data Stores
        DDB[(DynamoDB: products, reviews, graph edges)]
        OS[(OpenSearch)]
    end

    subgraph Bedrock
        EMB[Embeddings - real-time]
        GEN[Claude: generate]
        VER[Claude: verify citations]
    end

    subgraph Ingestion - see docs/designs/01
        S3RAW[(S3: raw data)]
        SF[Step Functions]
        ETL[Glue / Fargate ETL]
        BATCH[Bedrock Batch: embeddings]
    end

    FE --> APIGW --> LAMBDA
    LAMBDA --> EMB --> RTR
    RTR --> SRCH --> OS
    RTR --> IMG --> OS
    RTR --> GRF --> DDB
    RTR --> LKP --> DDB
    RTR --> GEN --> VER --> LAMBDA
    LAMBDA --> FE

    S3RAW --> SF --> ETL
    ETL --> DDB
    ETL --> BATCH --> OS
```

**Tech stack**

| Layer | Service | Notes |
| --- | --- | --- |
| Frontend | S3 + CloudFront | Static SPA |
| API | API Gateway + Lambda | Entry point, CORS to the SPA |
| Structured metadata | DynamoDB | Product / review records, pay-per-use |
| Hybrid retrieval | Amazon OpenSearch Service | BM25 + k-NN + structured filters, provisioned (not Serverless) |
| Knowledge graph | DynamoDB (adjacency-list table) | Category hierarchy, co-purchase, brand edges — not Neptune; this AWS account's plan doesn't support it (see [05](docs/designs/05-observability-cost.md)) |
| Embeddings + generation | Amazon Bedrock | Titan embeddings, Claude generation + verification |
| Ingestion / ETL | AWS Glue or Fargate + Step Functions | Batch load and periodic delta refresh |
| Region | us-east-2 | |

## Repository structure

```
docs/
  designs/     one detailed design doc per workflow: decisions + reasoning
  PROGRESS.md  what's implemented, what's next
src/           implementation, added as each workflow is built
tests/         unit tests, run without AWS credentials (moto-mocked)
infra/         AWS CDK (Python) app defining the infrastructure
```

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Infrastructure

AWS CDK (Python) — see [PROGRESS.md](docs/PROGRESS.md#infrastructure-infra) for what's defined so far. `cdk.json` points `cdk` at `../.venv/bin/python3`, so no need to activate the venv first — just make sure `pip install -e ".[infra]"` has been run in it at least once.

```bash
pip install -e ".[infra]"
cd infra && npm install
npx cdk synth          # generates CloudFormation templates, no AWS credentials needed
npx cdk bootstrap      # one-time per account/region, needs AWS credentials
npx cdk deploy --all   # needs AWS credentials configured, and starts real billing
npx cdk destroy --all  # tear down between work sessions - see the cost discipline in docs/designs/05-observability-cost.md
```

## Design docs

Each major workflow has its own design doc covering the architecture, the decisions made, and why:

- [01 — Ingestion & Refresh Pipeline](docs/designs/01-ingestion-pipeline.md)
- [02 — Retrieval Agents & Query Orchestration](docs/designs/02-retrieval-agents.md)
- [03 — Image Upload & Multimodal Query](docs/designs/03-image-upload.md)
- [04 — Inference Serving](docs/designs/04-inference-serving.md)
- [05 — Observability & Cost Controls](docs/designs/05-observability-cost.md)
- [06 — Frontend Hosting](docs/designs/06-frontend-hosting.md)

See [docs/PROGRESS.md](docs/PROGRESS.md) for implementation status.

## Experiments

Before implementing a pipeline stage that has real design choices to make, we run a small experiment rather than assume — results and the strategy actually chosen are recorded under [docs/experiments/](docs/experiments/):

- [01 — Chunking strategy](docs/experiments/01-chunking-strategy.md): compared fixed-size word-count splitting (with and without overlap) against sentence-aware splitting on 40 real reviews, measured by **% clean sentence boundary** (the fraction of chunks that end on a sentence boundary rather than mid-sentence — a proxy for whether a chunk reads as a coherent, self-contained unit). **Chosen: sentence-aware, 300-word budget** — same chunk density as the original default (1.1 chunks/review), but 100% clean boundaries vs. 90.9% for word-count splitting, and no overlap needed since nothing gets cut mid-sentence. Flagged to re-validate against precision@k and citation grounding rate (defined in [design doc 05](docs/designs/05-observability-cost.md#metrics-to-monitor)) once retrieval is live.
- [02 — Summarization prompting](docs/experiments/02-summarization-prompting.md): LLM-as-judge comparison of 3 candidate prompts for summarizing long reviews before chunking. **Blocked** on a one-time Bedrock model-access step on this AWS account; not yet run — the current default prompt stays until it is.

## Development principles

- Direct, clean code — no speculative abstractions or unused flexibility
- Comments only where the *why* isn't obvious from the code itself (a constraint, a workaround, a non-obvious invariant) — not restating what the code does
- No debug/trace logging left in committed code; structured logs only at meaningful operational boundaries (a request handled, a pipeline stage completed, an error)

## Cost

Target: under $100/month total AWS spend, which is the dominant constraint behind several choices in this design (provisioned over Serverless, a 5,000-product dataset, smallest instance sizes throughout). See [Observability & Cost Controls](docs/designs/05-observability-cost.md).

# Progress

Tracks implementation status per workflow. Update this file as each workflow moves from designed to implemented.

| Workflow | Design | Implementation | Status |
| --- | --- | --- | --- |
| Ingestion & Refresh Pipeline | [01](designs/01-ingestion-pipeline.md) | [src/ingestion](../src/ingestion) | In progress |
| Retrieval Agents & Query Orchestration | [02](designs/02-retrieval-agents.md) | — | Not started |
| Image Upload & Multimodal Query | [03](designs/03-image-upload.md) | — | Not started |
| Inference Serving | [04](designs/04-inference-serving.md) | — | Not started |
| Observability & Cost Controls | [05](designs/05-observability-cost.md) | — | Not started |
| Frontend Hosting | [06](designs/06-frontend-hosting.md) | — | Not started |

## Ingestion & Refresh Pipeline — what's done

Implemented and unit-tested (12 tests, no AWS credentials needed — DynamoDB is moto-mocked, Claude calls are stubbed):
- `src/ingestion/models.py` — `Product`, `Review`, `Chunk`
- `src/ingestion/chunking.py` — description/review splitting with overlap, tag-summary generation (see [01](designs/01-ingestion-pipeline.md#chunking-strategy))
- `src/ingestion/summarization.py` — direct Claude call (via Bedrock) to summarize a long review before chunking, per the "Agents vs Direct LLM Calls" decision in [02](designs/02-retrieval-agents.md); injected as a dependency so `chunk_review` stays testable without live Bedrock access
- `src/ingestion/dynamo_writer.py` — idempotent product/review upserts
- `src/ingestion/opensearch_documents.py` — chunk-to-document mapping with the filter fields from [02](designs/02-retrieval-agents.md#opensearch-metadata--filtering), plus the index mapping

Not yet implemented:
- Bedrock Batch embedding manifest builder + invocation
- OpenSearch/Neptune bulk loader scripts (real client calls, not just document shaping)
- Neptune graph-edge builder (category hierarchy, co-purchase, brand)
- Step Functions state machine wiring the stages together
- Daily EventBridge trigger for delta refreshes

## Infrastructure (`infra/`)

**Resolved: AWS CDK (Python)** — one language across app code and infra, rather than adding Terraform/HCL as a second one.

`cdk synth` succeeds with no AWS credentials (verified — it's pure template generation); `cdk deploy` needs real credentials and starts real billing the moment it runs, so it hasn't been run.

| Stack | Resources | Status |
| --- | --- | --- |
| `RagEcommerce-Data` | S3 bucket (raw/processed/query-images prefixes), DynamoDB Products + Reviews tables | Written, synthesized, not deployed |
| `RagEcommerce-Search` | Single-node OpenSearch domain, smallest instance | Written, synthesized, not deployed |
| `RagEcommerce-Graph` | Neptune cluster + instance, in a NAT-free isolated VPC (a NAT gateway alone would cost ~$32/month) | Written, synthesized, not deployed |

Every resource uses `RemovalPolicy.DESTROY` so `cdk destroy` fully tears the stack down between work sessions, per the cost discipline in [05](designs/05-observability-cost.md).

## Open decisions (not yet resolved)

- AWS credential setup on this machine for the target account/region — required before `cdk deploy` can run
- Whether the demo needs to be always-live or can be brought up on demand (affects whether the DynamoDB-graph swap for Neptune is worth doing up front — see [05](designs/05-observability-cost.md))

## Suggested build order

1. Ingestion & Refresh Pipeline — nothing else has data to work with until this exists
2. Retrieval Agents & Query Orchestration — the core of the product
3. Inference Serving details (retry/timeout/streaming) — harden alongside #2
4. Image Upload & Multimodal Query
5. Observability & Cost Controls — instrument as each piece above lands, not after the fact
6. Frontend Hosting — last, once there's an API worth putting a UI in front of

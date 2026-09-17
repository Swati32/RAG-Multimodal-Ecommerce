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

Implemented and unit-tested (9 tests, no AWS credentials needed — DynamoDB is moto-mocked):
- `src/ingestion/models.py` — `Product`, `Review`, `Chunk`
- `src/ingestion/chunking.py` — description/review splitting with overlap, tag-summary generation (see [01](designs/01-ingestion-pipeline.md#chunking-strategy))
- `src/ingestion/dynamo_writer.py` — idempotent product/review upserts
- `src/ingestion/opensearch_documents.py` — chunk-to-document mapping with the filter fields from [02](designs/02-retrieval-agents.md#opensearch-metadata--filtering), plus the index mapping

Not yet implemented:
- Bedrock Batch embedding manifest builder + invocation
- OpenSearch/Neptune bulk loader scripts (real client calls, not just document shaping)
- Neptune graph-edge builder (category hierarchy, co-purchase, brand)
- Step Functions state machine wiring the stages together
- Delta-batch/refresh-cadence driver
- Infrastructure as code for any of the above (blocked on the two open decisions below)

## Open decisions (not yet resolved)

- IaC tool: Terraform vs AWS CDK
- AWS credential setup on this machine for the target account/region
- Whether the demo needs to be always-live or can be brought up on demand (affects whether the DynamoDB-graph swap for Neptune is worth doing up front — see [05](designs/05-observability-cost.md))

## Suggested build order

1. Ingestion & Refresh Pipeline — nothing else has data to work with until this exists
2. Retrieval Agents & Query Orchestration — the core of the product
3. Inference Serving details (retry/timeout/streaming) — harden alongside #2
4. Image Upload & Multimodal Query
5. Observability & Cost Controls — instrument as each piece above lands, not after the fact
6. Frontend Hosting — last, once there's an API worth putting a UI in front of

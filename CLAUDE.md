# CLAUDE.md

## Implementation Fidelity
- Follow the design docs (docs/designs/) as written - pipeline stages specced as Glue jobs must be implemented as real AWS Glue jobs, not local scripts standing in for them
- If an implementation must deviate from what a design doc says (scope cut, different approach, skipped step), say so explicitly rather than silently substituting - flag the deviation and why, don't let docs and reality drift apart quietly

## Environment
- AWS auth uses `aws login` (session-based, expires ~hourly) - reauthenticate with `aws login`, not `aws configure`
- `cd infra && npx cdk <synth|deploy|destroy>` - CDK CLI is a local npm devDependency, not global; `cdk.json` points the app at `../.venv/bin/python3` directly, so the venv doesn't need activating first
- Bedrock Claude models need `us.<model-id>` inference-profile IDs for `invoke_model` (e.g. `us.anthropic.claude-haiku-4-5-20251001-v1:0`), not bare model IDs
- This AWS account (953146692069) doesn't support Amazon Neptune (plan restriction, `CREATE_FAILED`) - graph storage is a DynamoDB adjacency-list table instead, see docs/designs/01-ingestion-pipeline.md
- `aws dynamodb describe-table`'s `ItemCount` is stale (~6h refresh) - use `aws dynamodb scan --select COUNT` for a live count

## Coding Guidelines
- Clean code: direct and readable over clever; no speculative abstractions or unused flexibility
- Modular: small, single-purpose functions/modules that compose (see src/ingestion/ - models, chunking, summarization, dynamo_writer, opensearch_documents are each separate)
- Reuse existing code/modules before writing new logic that duplicates it
- No excessive logging - no debug/trace prints left in committed code; structured logs only at meaningful operational boundaries
- No excessive comments - only where the *why* isn't obvious from the code itself (a constraint, a workaround, a non-obvious invariant), never restating what the code does

## Testing
- `pip install -e ".[dev]"` then `pytest` - no AWS credentials needed; DynamoDB is mocked via `moto`, Claude/Bedrock calls via injected stub clients

## Documentation
- Any doc reporting eval results (README or docs/) must define each metric inline (precision@k, grounding rate, etc.), not just name it
- Mermaid flowcharts: always `%%{init: {'flowchart': {'curve': 'linear'}}}%%` for straight-line edges, never the default curved splines

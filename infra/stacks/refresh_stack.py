from aws_cdk import Duration, Stack
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct


class RefreshStack(Stack):
    """Step Functions orchestration + daily EventBridge trigger for the
    incremental refresh cadence - see docs/designs/01-ingestion-
    pipeline.md, "Simulating refresh cadence". Sequences stages 2-5 (not
    stage 1, the one-time initial catalog load) against a small delta
    batch of genuinely new reviews each run, not a full reprocessing pass
    - each Glue job's `--reviews_input_key`/`--input_key`/`--output_key`
    is overridden here to point at delta-specific S3 paths instead of the
    full-corpus ones, reusing the same job definitions from GlueStack
    without any job-level code duplication.

    Graph edges are rebuilt in full on every run rather than incrementally
    - see build_graph_edges.py: at ~100s for the full 5,000-product
    catalog, a full rebuild is cheap enough not to need delta-specific
    edge logic, unlike the embedding stage.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        load_delta_reviews = tasks.GlueStartJobRun(
            self,
            "LoadDeltaReviews",
            glue_job_name="rag-ecommerce-load-delta-reviews",
            integration_pattern=sfn.IntegrationPattern.RUN_JOB,
        )
        chunk_delta_reviews = tasks.GlueStartJobRun(
            self,
            "ChunkDeltaReviews",
            glue_job_name="rag-ecommerce-chunk-and-summarize",
            integration_pattern=sfn.IntegrationPattern.RUN_JOB,
            arguments=sfn.TaskInput.from_object(
                {
                    "--reviews_input_key": "processed/delta/reviews.jsonl",
                    "--output_key": "processed/delta/chunks.jsonl",
                }
            ),
        )
        embed_delta_chunks = tasks.GlueStartJobRun(
            self,
            "EmbedDeltaChunks",
            glue_job_name="rag-ecommerce-embed-chunks",
            integration_pattern=sfn.IntegrationPattern.RUN_JOB,
            arguments=sfn.TaskInput.from_object(
                {
                    "--input_key": "processed/delta/chunks.jsonl",
                    "--output_key": "processed/delta/embedded_chunks.jsonl",
                }
            ),
        )
        load_delta_into_opensearch = tasks.GlueStartJobRun(
            self,
            "LoadDeltaIntoOpenSearch",
            glue_job_name="rag-ecommerce-load-opensearch",
            integration_pattern=sfn.IntegrationPattern.RUN_JOB,
            arguments=sfn.TaskInput.from_object({"--input_key": "processed/delta/embedded_chunks.jsonl"}),
        )
        rebuild_graph_edges = tasks.GlueStartJobRun(
            self,
            "RebuildGraphEdges",
            glue_job_name="rag-ecommerce-build-graph-edges",
            integration_pattern=sfn.IntegrationPattern.RUN_JOB,
        )

        definition = load_delta_reviews.next(chunk_delta_reviews).next(embed_delta_chunks).next(
            load_delta_into_opensearch
        ).next(rebuild_graph_edges)

        self.state_machine = sfn.StateMachine(
            self,
            "RefreshStateMachine",
            state_machine_name="rag-ecommerce-daily-refresh",
            definition=definition,
            timeout=Duration.hours(2),
        )

        events.Rule(
            self,
            "DailyRefreshSchedule",
            rule_name="rag-ecommerce-daily-refresh-schedule",
            schedule=events.Schedule.rate(Duration.days(1)),
            targets=[targets.SfnStateMachine(self.state_machine)],
        )

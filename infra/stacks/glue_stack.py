from pathlib import Path

from aws_cdk import Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_glue as glue
from aws_cdk import aws_iam as iam
from aws_cdk import aws_opensearchservice as opensearch
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_assets as s3_assets
from constructs import Construct

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# One zip for the whole `ingestion` package (plus dataset_source.py) - Python
# Shell's --extra-py-files only reliably picks up zip/egg files, not loose
# .py files, so everything lives together in src/ for this to work. Shared
# across every job in this stack rather than re-uploaded per job.
_MODULES_ASSET_ID = "IngestionModules"


class GlueStack(Stack):
    """Glue Python Shell jobs for the ingestion pipeline - see
    docs/designs/01-ingestion-pipeline.md. Python Shell, not Spark ETL:
    right-sized for this dataset, billed only while running.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        data_bucket: s3.IBucket,
        products_table: dynamodb.ITableV2,
        reviews_table: dynamodb.ITableV2,
        graph_edges_table: dynamodb.ITableV2,
        search_domain: opensearch.IDomain,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self._modules_asset = s3_assets.Asset(self, _MODULES_ASSET_ID, path=str(REPO_ROOT / "src"))

        self.role = iam.Role(
            self,
            "GlueJobRole",
            assumed_by=iam.ServicePrincipal("glue.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSGlueServiceRole")],
        )
        self._modules_asset.grant_read(self.role)
        products_table.grant_read_write_data(self.role)
        reviews_table.grant_read_write_data(self.role)
        graph_edges_table.grant_read_write_data(self.role)
        data_bucket.grant_read_write(self.role)
        search_domain.grant_read_write(self.role)
        self.role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:*:{self.account}:inference-profile/*",
                ],
            )
        )

        self.load_dataset_job = self._python_shell_job(
            "LoadDatasetJob",
            job_name="rag-ecommerce-load-dataset",
            script_path=REPO_ROOT / "infra/glue_scripts/load_dataset.py",
            default_arguments={
                "--additional-python-modules": "requests,huggingface_hub",
                "--products_table": products_table.table_name,
                "--reviews_table": reviews_table.table_name,
                "--limit": "5000",
                "--max_reviews_per_product": "3",
            },
        )

        self.chunk_and_summarize_job = self._python_shell_job(
            "ChunkAndSummarizeJob",
            job_name="rag-ecommerce-chunk-and-summarize",
            script_path=REPO_ROOT / "infra/glue_scripts/chunk_and_summarize.py",
            default_arguments={
                # The Glue Python Shell environment bundles a 2022-era boto3/
                # botocore that predates Bedrock's service model entirely
                # ("Unknown service: 'bedrock-runtime'") - force a current one.
                "--additional-python-modules": "boto3>=1.34",
                "--products_table": products_table.table_name,
                "--reviews_table": reviews_table.table_name,
                "--output_bucket": data_bucket.bucket_name,
                "--output_key": "processed/chunks.jsonl",
                "--claude_model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                # Empty by default - "" means full mode (scan DynamoDB).
                # The daily refresh state machine overrides this to a
                # delta-reviews S3 key to switch into delta mode.
                "--reviews_input_key": "",
            },
        )

        self.embed_chunks_job = self._python_shell_job(
            "EmbedChunksJob",
            job_name="rag-ecommerce-embed-chunks",
            script_path=REPO_ROOT / "infra/glue_scripts/embed_chunks.py",
            default_arguments={
                "--additional-python-modules": "boto3>=1.34",
                "--input_bucket": data_bucket.bucket_name,
                "--input_key": "processed/chunks.jsonl",
                "--output_bucket": data_bucket.bucket_name,
                "--output_key": "processed/embedded_chunks.jsonl",
                "--embedding_model_id": "amazon.titan-embed-text-v2:0",
            },
            # Rate-limited to 540 req/min (see embed_chunks.py) - ~20k chunks
            # takes ~40 minutes there, so the default 60-minute timeout
            # leaves too little margin for retries.
            timeout_minutes=90,
        )

        self.load_opensearch_job = self._python_shell_job(
            "LoadOpenSearchJob",
            job_name="rag-ecommerce-load-opensearch",
            script_path=REPO_ROOT / "infra/glue_scripts/load_opensearch.py",
            default_arguments={
                # opensearch-py's Glue-bundled version (1.1.0) predates
                # AWSV4SignerAuth - same category of gotcha as boto3 above.
                "--additional-python-modules": "boto3>=1.34,opensearch-py>=2.4",
                "--input_bucket": data_bucket.bucket_name,
                "--input_key": "processed/embedded_chunks.jsonl",
                "--products_table": products_table.table_name,
                "--opensearch_endpoint": search_domain.domain_endpoint,
                "--index_name": "chunks",
            },
            timeout_minutes=90,
        )

        self.build_graph_edges_job = self._python_shell_job(
            "BuildGraphEdgesJob",
            job_name="rag-ecommerce-build-graph-edges",
            script_path=REPO_ROOT / "infra/glue_scripts/build_graph_edges.py",
            default_arguments={
                "--products_table": products_table.table_name,
                "--reviews_table": reviews_table.table_name,
                "--graph_edges_table": graph_edges_table.table_name,
            },
        )

        self.embed_images_job = self._python_shell_job(
            "EmbedImagesJob",
            job_name="rag-ecommerce-embed-images",
            script_path=REPO_ROOT / "infra/glue_scripts/embed_images.py",
            default_arguments={
                "--additional-python-modules": "boto3>=1.34,requests",
                "--products_table": products_table.table_name,
                "--output_bucket": data_bucket.bucket_name,
                "--output_key": "processed/embedded_images.jsonl",
                "--embedding_model_id": "us.cohere.embed-v4:0",
            },
            # Rate-limited to 180 req/min (Cohere Embed v4's 200/min quota) -
            # 5,000 images plus per-image download time needs real margin.
            timeout_minutes=90,
        )

        self.load_images_job = self._python_shell_job(
            "LoadImagesJob",
            job_name="rag-ecommerce-load-images",
            script_path=REPO_ROOT / "infra/glue_scripts/load_images.py",
            default_arguments={
                "--additional-python-modules": "boto3>=1.34,opensearch-py>=2.4",
                "--input_bucket": data_bucket.bucket_name,
                "--input_key": "processed/embedded_images.jsonl",
                "--products_table": products_table.table_name,
                "--opensearch_endpoint": search_domain.domain_endpoint,
                "--index_name": "product_images",
            },
            timeout_minutes=90,
        )

        self.load_delta_reviews_job = self._python_shell_job(
            "LoadDeltaReviewsJob",
            job_name="rag-ecommerce-load-delta-reviews",
            script_path=REPO_ROOT / "infra/glue_scripts/load_delta_reviews.py",
            default_arguments={
                "--additional-python-modules": "requests,huggingface_hub",
                "--products_table": products_table.table_name,
                "--reviews_table": reviews_table.table_name,
                "--output_bucket": data_bucket.bucket_name,
                "--output_key": "processed/delta/reviews.jsonl",
                # Small and bounded, spread across many products - a daily
                # delta, not a reprocessing pass. See docs/designs/
                # 01-ingestion-pipeline.md, "Simulating refresh cadence".
                "--max_new_reviews": "200",
                "--max_new_reviews_per_product": "1",
            },
        )

    def _python_shell_job(
        self,
        construct_id: str,
        *,
        job_name: str,
        script_path: Path,
        default_arguments: dict,
        timeout_minutes: int = 60,
    ) -> glue.CfnJob:
        script_asset = s3_assets.Asset(self, f"{construct_id}Script", path=str(script_path))
        script_asset.grant_read(self.role)

        return glue.CfnJob(
            self,
            construct_id,
            name=job_name,
            role=self.role.role_arn,
            command=glue.CfnJob.JobCommandProperty(
                name="pythonshell",
                python_version="3.9",
                script_location=script_asset.s3_object_url,
            ),
            default_arguments={"--extra-py-files": self._modules_asset.s3_object_url, **default_arguments},
            max_capacity=1,
            glue_version="3.0",
            timeout=timeout_minutes,
        )

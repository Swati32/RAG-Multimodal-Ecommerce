from pathlib import Path

from aws_cdk import Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_glue as glue
from aws_cdk import aws_iam as iam
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
        data_bucket.grant_read_write(self.role)
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
            },
        )

    def _python_shell_job(self, construct_id: str, *, job_name: str, script_path: Path, default_arguments: dict) -> glue.CfnJob:
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
            timeout=60,
        )

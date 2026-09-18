from pathlib import Path

from aws_cdk import Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_glue as glue
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3_assets as s3_assets
from constructs import Construct

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


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
        products_table: dynamodb.ITableV2,
        reviews_table: dynamodb.ITableV2,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        script_asset = s3_assets.Asset(
            self, "LoadDatasetScript", path=str(REPO_ROOT / "infra/glue_scripts/load_dataset.py")
        )
        # One zip for both `ingestion` and `dataset_source` - Python Shell's
        # --extra-py-files only reliably picks up zip/egg files, not loose
        # .py files, so they live side by side in src/ for this to work.
        modules_asset = s3_assets.Asset(self, "IngestionModules", path=str(REPO_ROOT / "src"))

        role = iam.Role(
            self,
            "GlueJobRole",
            assumed_by=iam.ServicePrincipal("glue.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSGlueServiceRole")],
        )
        for asset in (script_asset, modules_asset):
            asset.grant_read(role)
        products_table.grant_read_write_data(role)
        reviews_table.grant_read_write_data(role)

        self.load_dataset_job = glue.CfnJob(
            self,
            "LoadDatasetJob",
            name="rag-ecommerce-load-dataset",
            role=role.role_arn,
            command=glue.CfnJob.JobCommandProperty(
                name="pythonshell",
                python_version="3.9",
                script_location=script_asset.s3_object_url,
            ),
            default_arguments={
                "--extra-py-files": modules_asset.s3_object_url,
                "--additional-python-modules": "requests,huggingface_hub",
                "--products_table": products_table.table_name,
                "--reviews_table": reviews_table.table_name,
                "--limit": "5000",
                "--max_reviews_per_product": "3",
            },
            max_capacity=1,
            glue_version="3.0",
            timeout=60,
        )

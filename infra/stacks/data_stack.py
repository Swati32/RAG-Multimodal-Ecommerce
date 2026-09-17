from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_s3 as s3
from constructs import Construct


class DataStack(Stack):
    """S3 raw/processed storage and the DynamoDB tables from design doc 01.

    Every resource here is destroyable (RemovalPolicy.DESTROY) on purpose:
    see docs/designs/05-observability-cost.md - this stack is meant to be
    torn down between work sessions, not left running.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.data_bucket = s3.Bucket(
            self,
            "DataBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ExpireQueryImages",
                    prefix="query-images/",
                    expiration=Duration.days(1),
                )
            ],
        )

        self.products_table = dynamodb.TableV2(
            self,
            "ProductsTable",
            partition_key=dynamodb.Attribute(name="product_id", type=dynamodb.AttributeType.STRING),
            billing=dynamodb.Billing.on_demand(),
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.reviews_table = dynamodb.TableV2(
            self,
            "ReviewsTable",
            partition_key=dynamodb.Attribute(name="product_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="review_id", type=dynamodb.AttributeType.STRING),
            billing=dynamodb.Billing.on_demand(),
            removal_policy=RemovalPolicy.DESTROY,
        )

from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_opensearchservice as opensearch
from constructs import Construct


class SearchStack(Stack):
    """Single-node, smallest-instance OpenSearch domain.

    Provisioned, not Serverless - see docs/designs/05-observability-cost.md
    for why. Verify the instance type and pricing against current AWS
    documentation before deploying; this is sized for the $100/month cap,
    not for production traffic.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.domain = opensearch.Domain(
            self,
            "HybridSearchDomain",
            version=opensearch.EngineVersion.OPENSEARCH_2_11,
            capacity=opensearch.CapacityConfig(
                data_node_instance_type="t3.small.search",
                data_nodes=1,
                master_nodes=0,
            ),
            ebs=opensearch.EbsOptions(volume_size=10),
            zone_awareness=opensearch.ZoneAwarenessConfig(enabled=False),
            enforce_https=True,
            node_to_node_encryption=True,
            encryption_at_rest=opensearch.EncryptionAtRestOptions(enabled=True),
            removal_policy=RemovalPolicy.DESTROY,
            access_policies=[
                iam.PolicyStatement(
                    actions=["es:ESHttp*"],
                    principals=[iam.AccountPrincipal(self.account)],
                    resources=["*"],
                )
            ],
        )

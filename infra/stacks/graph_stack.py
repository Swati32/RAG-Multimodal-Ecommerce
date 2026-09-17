from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_neptune as neptune
from constructs import Construct


class GraphStack(Stack):
    """Smallest provisioned Neptune cluster, in an isolated (no-NAT) VPC.

    A NAT gateway bills ~$32/month on its own, which alone would threaten
    the $100/month cap - so subnets here are PRIVATE_ISOLATED and Neptune
    never needs outbound internet access. Verify the instance class against
    current AWS Neptune docs before deploying; see docs/designs/
    05-observability-cost.md for the cost reasoning, including the
    DynamoDB-adjacency-list alternative to this stack entirely.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.vpc = ec2.Vpc(
            self,
            "GraphVpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="isolated",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                )
            ],
        )

        self.security_group = ec2.SecurityGroup(
            self, "NeptuneSecurityGroup", vpc=self.vpc, allow_all_outbound=False
        )
        self.security_group.add_ingress_rule(
            ec2.Peer.ipv4(self.vpc.vpc_cidr_block), ec2.Port.tcp(8182)
        )

        subnet_group = neptune.CfnDBSubnetGroup(
            self,
            "NeptuneSubnetGroup",
            db_subnet_group_description="Isolated subnets for Neptune",
            subnet_ids=self.vpc.select_subnets(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ).subnet_ids,
        )

        self.cluster = neptune.CfnDBCluster(
            self,
            "GraphCluster",
            db_subnet_group_name=subnet_group.ref,
            vpc_security_group_ids=[self.security_group.security_group_id],
            deletion_protection=False,
        )
        self.cluster.apply_removal_policy(RemovalPolicy.DESTROY)

        self.instance = neptune.CfnDBInstance(
            self,
            "GraphInstance",
            db_instance_class="db.t3.medium",
            db_cluster_identifier=self.cluster.ref,
        )
        self.instance.apply_removal_policy(RemovalPolicy.DESTROY)

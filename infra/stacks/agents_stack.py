from pathlib import Path

from aws_cdk import Stack
from aws_cdk import aws_bedrockagentcore as bedrockagentcore
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_opensearchservice as opensearch
from constructs import Construct

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Source assets only - build context is the repo root (Dockerfiles need
# `COPY src/`), but tests/docs/scripts/etc. have no business being staged
# into a container image asset.
_ASSET_EXCLUDES = [".venv", "infra/cdk.out", "node_modules", ".git", "**/__pycache__", "tests", "docs", "scripts"]


class AgentsStack(Stack):
    """Retrieval specialist agents - see docs/designs/02-retrieval-agents.md.

    Hosted on Bedrock AgentCore Runtime, each running a hand-rolled Claude
    tool-use loop (src/agents/*_runtime.py) - not AWS Bedrock Agents
    (classic): that service is in maintenance mode and closed to new
    accounts (a real CreateAgent call returned "Bedrock Agents is in
    Maintenance Mode. New agent creation is not available for accounts
    without prior service usage"). See "Agent implementation" in the design
    doc for the full reasoning behind AgentCore over a plain Lambda.

    Built one agent at a time.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        products_table: dynamodb.ITableV2,
        reviews_table: dynamodb.ITableV2,
        graph_edges_table: dynamodb.ITableV2,
        search_domain: opensearch.IDomain,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.lookup_agent = self._build_lookup_agent(products_table, reviews_table)
        self.search_agent = self._build_search_agent(search_domain)
        self.graph_agent = self._build_graph_agent(graph_edges_table)
        self.image_agent = self._build_image_agent(search_domain)
        self.router = self._build_router(products_table, reviews_table)

    def _build_runtime(self, construct_id: str, *, runtime_name: str, dockerfile: str, environment_variables: dict) -> bedrockagentcore.Runtime:
        runtime = bedrockagentcore.Runtime(
            self,
            construct_id,
            runtime_name=runtime_name,
            agent_runtime_artifact=bedrockagentcore.AgentRuntimeArtifact.from_asset(
                str(REPO_ROOT),
                file=dockerfile,
                platform=ecr_assets.Platform.LINUX_ARM64,
                exclude=_ASSET_EXCLUDES,
            ),
            environment_variables=environment_variables,
        )
        runtime.add_to_role_policy(
            iam.PolicyStatement(
                # InvokeModelWithResponseStream is what Converse's streaming
                # variant (converse_stream, used by the router's generator -
                # see docs/designs/04-inference-serving.md) calls under the
                # hood - a separate permission from plain InvokeModel, not
                # implied by it (a real AccessDeniedException on
                # bedrock:InvokeModelWithResponseStream otherwise, even
                # though bedrock:InvokeModel already worked).
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=[
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:*:{self.account}:inference-profile/*",
                ],
            )
        )
        return runtime

    def _build_lookup_agent(
        self, products_table: dynamodb.ITableV2, reviews_table: dynamodb.ITableV2
    ) -> bedrockagentcore.Runtime:
        runtime = self._build_runtime(
            "LookupAgentRuntime",
            runtime_name="rag_ecommerce_lookup_agent",
            dockerfile="infra/agent_runtimes/lookup_agent/Dockerfile",
            environment_variables={
                "PRODUCTS_TABLE": products_table.table_name,
                "REVIEWS_TABLE": reviews_table.table_name,
            },
        )
        products_table.grant_read_data(runtime.role)
        reviews_table.grant_read_data(runtime.role)
        return runtime

    def _build_search_agent(self, search_domain: opensearch.IDomain) -> bedrockagentcore.Runtime:
        runtime = self._build_runtime(
            "SearchAgentRuntime",
            runtime_name="rag_ecommerce_search_agent",
            dockerfile="infra/agent_runtimes/search_agent/Dockerfile",
            environment_variables={"OPENSEARCH_ENDPOINT": search_domain.domain_endpoint},
        )
        # grant_read_write, not grant_read - SearchAgent PUTs the search
        # pipeline (idempotent setup) in addition to querying it, and the
        # domain's resource policy alone isn't sufficient: OpenSearch also
        # requires the calling role's own identity policy to allow the
        # action (confirmed by a real 403 with grant_read: "no identity-
        # based policy allows the es:ESHttpPut action").
        search_domain.grant_read_write(runtime.role)
        return runtime

    def _build_router(self, products_table: dynamodb.ITableV2, reviews_table: dynamodb.ITableV2) -> bedrockagentcore.Runtime:
        runtime = self._build_runtime(
            "RouterRuntime",
            runtime_name="rag_ecommerce_router",
            dockerfile="infra/agent_runtimes/router/Dockerfile",
            environment_variables={
                "SEARCH_AGENT_ARN": self.search_agent.agent_runtime_arn,
                "GRAPH_AGENT_ARN": self.graph_agent.agent_runtime_arn,
                "LOOKUP_AGENT_ARN": self.lookup_agent.agent_runtime_arn,
                "IMAGE_AGENT_ARN": self.image_agent.agent_runtime_arn,
                "PRODUCTS_TABLE": products_table.table_name,
                "REVIEWS_TABLE": reviews_table.table_name,
            },
        )
        for specialist in (self.search_agent, self.graph_agent, self.lookup_agent, self.image_agent):
            specialist.grant_invoke_runtime(runtime.role)
        # Citation resolution (title/image_url, plus a couple of real
        # reviews per product card) after the verifier - see
        # answer_generation.resolve_citations.
        products_table.grant_read_data(runtime.role)
        reviews_table.grant_read_data(runtime.role)
        return runtime

    def _build_graph_agent(self, graph_edges_table: dynamodb.ITableV2) -> bedrockagentcore.Runtime:
        runtime = self._build_runtime(
            "GraphAgentRuntime",
            runtime_name="rag_ecommerce_graph_agent",
            dockerfile="infra/agent_runtimes/graph_agent/Dockerfile",
            environment_variables={"GRAPH_EDGES_TABLE": graph_edges_table.table_name},
        )
        graph_edges_table.grant_read_data(runtime.role)
        return runtime

    def _build_image_agent(self, search_domain: opensearch.IDomain) -> bedrockagentcore.Runtime:
        runtime = self._build_runtime(
            "ImageAgentRuntime",
            runtime_name="rag_ecommerce_image_agent",
            dockerfile="infra/agent_runtimes/image_agent/Dockerfile",
            environment_variables={"OPENSEARCH_ENDPOINT": search_domain.domain_endpoint},
        )
        # grant_read_write, not grant_read - grant_read doesn't cover
        # es:ESHttpPost, and opensearch-py sends an ordinary `_search` with
        # a request body as a POST, not a GET (confirmed by a real 403:
        # "no identity-based policy allows the es:ESHttpPost action"). See
        # the same category of gotcha on SearchAgent's role, above.
        search_domain.grant_read_write(runtime.role)
        return runtime

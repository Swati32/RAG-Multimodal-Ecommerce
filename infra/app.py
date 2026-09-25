#!/usr/bin/env python3
import os

import aws_cdk as cdk

from stacks.agents_stack import AgentsStack
from stacks.data_stack import DataStack
from stacks.frontend_stack import FrontendStack
from stacks.glue_stack import GlueStack
from stacks.observability_stack import ObservabilityStack
from stacks.refresh_stack import RefreshStack
from stacks.search_stack import SearchStack
from stacks.upload_stack import UploadStack

app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-2"),
)

# Cost Explorer isolation - see docs/designs/05-observability-cost.md,
# "Guardrails" ("tag every resource with a project tag").
cdk.Tags.of(app).add("Project", "rag-ecommerce")

data_stack = DataStack(app, "RagEcommerce-Data", env=env)
search_stack = SearchStack(app, "RagEcommerce-Search", env=env)
glue_stack = GlueStack(
    app,
    "RagEcommerce-Glue",
    data_bucket=data_stack.data_bucket,
    products_table=data_stack.products_table,
    reviews_table=data_stack.reviews_table,
    graph_edges_table=data_stack.graph_edges_table,
    search_domain=search_stack.domain,
    env=env,
)
refresh_stack = RefreshStack(app, "RagEcommerce-Refresh", env=env)
refresh_stack.add_dependency(glue_stack)
agents_stack = AgentsStack(
    app,
    "RagEcommerce-Agents",
    products_table=data_stack.products_table,
    reviews_table=data_stack.reviews_table,
    graph_edges_table=data_stack.graph_edges_table,
    search_domain=search_stack.domain,
    env=env,
)
upload_stack = UploadStack(
    app,
    "RagEcommerce-Upload",
    data_bucket=data_stack.data_bucket,
    router_runtime=agents_stack.router,
    env=env,
)
upload_stack.add_dependency(agents_stack)

observability_stack = ObservabilityStack(
    app,
    "RagEcommerce-Observability",
    alert_email="swati.sisodia61@gmail.com",
    dynamodb_table_names=[data_stack.products_table.table_name, data_stack.reviews_table.table_name, data_stack.graph_edges_table.table_name],
    reviews_table_name=data_stack.reviews_table.table_name,
    opensearch_domain_name=search_stack.domain.domain_name,
    state_machine_arn=refresh_stack.state_machine.state_machine_arn,
    api_lambda_functions=[upload_stack.presign_upload_fn, upload_stack.submit_query_fn],
    env=env,
)
observability_stack.add_dependency(upload_stack)

FrontendStack(app, "RagEcommerce-Frontend", env=env)

app.synth()

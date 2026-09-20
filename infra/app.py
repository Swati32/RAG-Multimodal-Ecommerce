#!/usr/bin/env python3
import os

import aws_cdk as cdk

from stacks.data_stack import DataStack
from stacks.glue_stack import GlueStack
from stacks.search_stack import SearchStack

app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-2"),
)

data_stack = DataStack(app, "RagEcommerce-Data", env=env)
search_stack = SearchStack(app, "RagEcommerce-Search", env=env)
GlueStack(
    app,
    "RagEcommerce-Glue",
    data_bucket=data_stack.data_bucket,
    products_table=data_stack.products_table,
    reviews_table=data_stack.reviews_table,
    search_domain=search_stack.domain,
    env=env,
)

app.synth()

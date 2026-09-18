#!/usr/bin/env python3
import os

import aws_cdk as cdk

from stacks.data_stack import DataStack
from stacks.search_stack import SearchStack

app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-2"),
)

DataStack(app, "RagEcommerce-Data", env=env)
SearchStack(app, "RagEcommerce-Search", env=env)

app.synth()

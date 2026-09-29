#!/usr/bin/env python3
"""CDK app: Entra OBO FastMCP on Amazon Bedrock AgentCore Runtime."""

import os

import aws_cdk as cdk

from stack import EntraOboMcpStack

app = cdk.App()

EntraOboMcpStack(
    app,
    "EntraOboMcpStack",
    env=cdk.Environment(
        account=os.getenv("CDK_DEFAULT_ACCOUNT"),
        region=os.getenv("CDK_DEFAULT_REGION", "us-east-1"),
    ),
    description="Entra ID On-Behalf-Of FastMCP server on AgentCore Runtime",
)

app.synth()

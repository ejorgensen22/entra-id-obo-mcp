"""Deploy entra-id-obo-mcp to Amazon Bedrock AgentCore Runtime with Entra JWT inbound auth."""

from __future__ import annotations

import os
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    CfnOutput,
    Stack,
    aws_ecr_assets as ecr_assets,
    aws_iam as iam,
    aws_secretsmanager as secretsmanager,
    aws_bedrockagentcore as agentcore,
)
from constructs import Construct

REPO_ROOT = Path(__file__).resolve().parents[2]


class EntraOboMcpStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        tenant_id = _ctx(self, "entra_tenant_id", "ENTRA_TENANT_ID")
        client_id = _ctx(self, "entra_client_id", "ENTRA_CLIENT_ID")
        identifier_uri = (
            self.node.try_get_context("entra_identifier_uri")
            or os.getenv("ENTRA_IDENTIFIER_URI")
            or f"api://{client_id}"
        )
        mcp_scope = (
            self.node.try_get_context("entra_mcp_scope")
            or os.getenv("ENTRA_MCP_SCOPE")
            or "access_as_user"
        )
        secret_name = (
            self.node.try_get_context("entra_secret_name")
            or os.getenv("ENTRA_SECRET_NAME")
            or "entra-obo-mcp/client-secret"
        )
        runtime_name = (
            self.node.try_get_context("runtime_name")
            or os.getenv("AGENTCORE_RUNTIME_NAME")
            or "entra_obo_mcp"
        )

        if not tenant_id or not client_id:
            raise ValueError(
                "Set entra_tenant_id and entra_client_id CDK context, or "
                "ENTRA_TENANT_ID and ENTRA_CLIENT_ID environment variables."
            )

        secret = secretsmanager.Secret.from_secret_name_v2(
            self, "EntraClientSecret", secret_name
        )

        image = ecr_assets.DockerImageAsset(
            self,
            "McpImage",
            directory=str(REPO_ROOT),
            file="Dockerfile",
            platform=ecr_assets.Platform.LINUX_ARM64,
        )

        role = iam.Role(
            self,
            "RuntimeRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            description="AgentCore Runtime role for entra-id-obo-mcp",
        )
        image.repository.grant_pull(role)
        secret.grant_read(role)
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams",
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "cloudwatch:PutMetricData",
                ],
                resources=["*"],
            )
        )

        runtime = agentcore.CfnRuntime(
            self,
            "McpRuntime",
            agent_runtime_name=runtime_name,
            description="Entra ID OBO FastMCP server",
            role_arn=role.role_arn,
            agent_runtime_artifact=agentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                container_configuration=agentcore.CfnRuntime.ContainerConfigurationProperty(
                    container_uri=image.image_uri,
                )
            ),
            protocol_configuration=agentcore.CfnRuntime.ProtocolConfigurationProperty(
                server_protocol="MCP",
            ),
            network_configuration=agentcore.CfnRuntime.NetworkConfigurationProperty(
                network_mode="PUBLIC",
            ),
            authorizer_configuration=agentcore.CfnRuntime.AuthorizerConfigurationProperty(
                custom_jwt_authorizer=agentcore.CfnRuntime.CustomJWTAuthorizerConfigurationProperty(
                    discovery_url=(
                        f"https://login.microsoftonline.com/{tenant_id}"
                        "/v2.0/.well-known/openid-configuration"
                    ),
                    allowed_audience=[client_id, identifier_uri],
                    allowed_scopes=[mcp_scope],
                )
            ),
            request_header_configuration=agentcore.CfnRuntime.RequestHeaderConfigurationProperty(
                request_header_allowlist=["Authorization"],
            ),
            environment_variables={
                "AUTH_MODE": "agentcore",
                "HOST": "0.0.0.0",
                "PORT": "8000",
                "ENTRA_TENANT_ID": tenant_id,
                "ENTRA_CLIENT_ID": client_id,
                "ENTRA_IDENTIFIER_URI": identifier_uri,
                "ENTRA_MCP_SCOPE": mcp_scope,
                "ENTRA_CLIENT_SECRET_ARN": secret.secret_arn,
                "GRAPH_SCOPES": (
                    "https://graph.microsoft.com/User.Read "
                    "https://graph.microsoft.com/Mail.Read"
                ),
            },
        )
        runtime.node.add_dependency(image)
        runtime.node.add_dependency(role)

        encoded_arn = cdk.Fn.join("%3A", cdk.Fn.split(":", runtime.attr_agent_runtime_arn))
        encoded_arn = cdk.Fn.join("%2F", cdk.Fn.split("/", encoded_arn))

        invoke_url = cdk.Fn.join(
            "",
            [
                "https://bedrock-agentcore.",
                self.region,
                ".amazonaws.com/runtimes/",
                encoded_arn,
                "/invocations?qualifier=DEFAULT",
            ],
        )

        CfnOutput(self, "AgentRuntimeArn", value=runtime.attr_agent_runtime_arn)
        CfnOutput(self, "InvokeUrl", value=invoke_url)
        CfnOutput(self, "ImageUri", value=image.image_uri)
        CfnOutput(
            self,
            "OidcDiscoveryUrl",
            value=(
                f"https://login.microsoftonline.com/{tenant_id}"
                "/v2.0/.well-known/openid-configuration"
            ),
        )
        CfnOutput(self, "SecretName", value=secret_name)


def _ctx(stack: Stack, key: str, env_name: str) -> str:
    value = stack.node.try_get_context(key) or os.getenv(env_name) or ""
    return str(value).strip()

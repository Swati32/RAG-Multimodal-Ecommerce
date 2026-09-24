from pathlib import Path

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_bedrockagentcore as bedrockagentcore
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from constructs import Construct

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ASSET_EXCLUDES = [".venv", "infra/cdk.out", "node_modules", ".git", "**/__pycache__", "tests", "docs", "scripts"]


class UploadStack(Stack):
    """Image upload + query API - see docs/designs/03-image-upload.md.

    Two Lambdas behind an HTTP API, sharing one Docker image (COPY src/,
    same pattern as the AgentCore runtime Dockerfiles) with the actual
    handler picked per function via DockerImageCode's `cmd` override, so
    the image is only built once for both:
    - POST /upload-url: presigns an S3 POST for a query image, scoped to
      the `query-images/` prefix DataStack already put a 1-day expiry
      lifecycle rule on
    - POST /query: accepts a prompt and/or an uploaded image's object_key,
      fetches the image from S3 if given, and invokes the router's
      AgentCore Runtime, collapsing its streamed SSE response into one
      JSON answer (see src/api/submit_query.py for why)
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        data_bucket: s3.IBucket,
        router_runtime: bedrockagentcore.Runtime,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Both functions point `from_image_asset` at the same directory/
        # Dockerfile/excludes - CDK hashes the build context, not the `cmd`
        # override, so this builds and pushes the underlying image once and
        # reuses it for both, only the per-function Lambda command differs.
        presign_upload_fn = lambda_.DockerImageFunction(
            self,
            "PresignUploadFunction",
            code=lambda_.DockerImageCode.from_image_asset(
                str(REPO_ROOT),
                file="infra/lambda_containers/query_api/Dockerfile",
                platform=ecr_assets.Platform.LINUX_ARM64,
                exclude=_ASSET_EXCLUDES,
                cmd=["api.presign_upload_handler.handler"],
            ),
            architecture=lambda_.Architecture.ARM_64,
            timeout=Duration.seconds(10),
            environment={"UPLOAD_BUCKET": data_bucket.bucket_name},
        )
        # The presigned POST inherits this role's permissions at the time a
        # client actually uses it - grant_write here is what makes the
        # eventual client upload succeed, not just the presigning call
        # itself (which needs no IAM permission, it's local SigV4 signing).
        data_bucket.grant_write(presign_upload_fn, "query-images/*")

        submit_query_fn = lambda_.DockerImageFunction(
            self,
            "SubmitQueryFunction",
            code=lambda_.DockerImageCode.from_image_asset(
                str(REPO_ROOT),
                file="infra/lambda_containers/query_api/Dockerfile",
                platform=ecr_assets.Platform.LINUX_ARM64,
                exclude=_ASSET_EXCLUDES,
                cmd=["api.submit_query_handler.handler"],
            ),
            architecture=lambda_.Architecture.ARM_64,
            # A full router request can take a while: routing + parallel
            # specialist dispatch + streamed generation + verification -
            # comfortably longer than any single Bedrock call's own 10-20s
            # timeout (see docs/designs/04-inference-serving.md).
            timeout=Duration.seconds(60),
            environment={"UPLOAD_BUCKET": data_bucket.bucket_name, "ROUTER_ARN": router_runtime.agent_runtime_arn},
        )
        data_bucket.grant_read(submit_query_fn, "query-images/*")
        router_runtime.grant_invoke_runtime(submit_query_fn)

        http_api = apigwv2.HttpApi(
            self,
            "QueryApi",
            cors_preflight=apigwv2.CorsPreflightOptions(
                allow_origins=["*"],
                allow_methods=[apigwv2.CorsHttpMethod.POST, apigwv2.CorsHttpMethod.OPTIONS],
                allow_headers=["content-type"],
            ),
        )
        http_api.add_routes(
            path="/upload-url",
            methods=[apigwv2.HttpMethod.POST],
            integration=integrations.HttpLambdaIntegration("PresignUploadIntegration", presign_upload_fn),
        )
        http_api.add_routes(
            path="/query",
            methods=[apigwv2.HttpMethod.POST],
            integration=integrations.HttpLambdaIntegration("SubmitQueryIntegration", submit_query_fn),
        )
        CfnOutput(self, "QueryApiUrl", value=http_api.api_endpoint)

from pathlib import Path

from aws_cdk import CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3_deployment
from constructs import Construct

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class FrontendStack(Stack):
    """Static SPA hosting - see docs/designs/06-frontend-hosting.md.

    A private S3 bucket (no public access, no static-website-hosting mode)
    behind CloudFront with Origin Access Control - CloudFront is the only
    thing allowed to read the bucket, matching the block-all-public-access
    posture already used for the data bucket elsewhere in this project.
    `BucketDeployment` uploads `frontend/dist` (the Vite build output,
    built locally before `cdk deploy` - this stack doesn't run the build
    itself) and invalidates the distribution's cache on every deploy so a
    new build is actually served, not a stale cached one.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        site_bucket = s3.Bucket(
            self,
            "SiteBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        distribution = cloudfront.Distribution(
            self,
            "Distribution",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            ),
            # This is a single-page app with no client-side routes today,
            # but a 404 from a bad/stale asset URL should still resolve to
            # the app shell rather than a raw S3 XML error page.
            error_responses=[
                cloudfront.ErrorResponse(http_status=403, response_http_status=200, response_page_path="/index.html"),
                cloudfront.ErrorResponse(http_status=404, response_http_status=200, response_page_path="/index.html"),
            ],
        )

        s3_deployment.BucketDeployment(
            self,
            "DeploySite",
            sources=[s3_deployment.Source.asset(str(REPO_ROOT / "frontend" / "dist"))],
            destination_bucket=site_bucket,
            distribution=distribution,
            distribution_paths=["/*"],
        )

        CfnOutput(self, "SiteUrl", value=f"https://{distribution.domain_name}")

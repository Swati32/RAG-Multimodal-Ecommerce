# Shared image for both API Lambdas (presign-upload, submit-query) - see
# infra/stacks/upload_stack.py, which sets the actual handler per function
# via DockerImageCode.from_image_asset(cmd=[...]). Build context is the
# repo root (needs `COPY src/`), same as the agent runtime Dockerfiles.
FROM public.ecr.aws/lambda/python:3.13

COPY src/ ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir boto3

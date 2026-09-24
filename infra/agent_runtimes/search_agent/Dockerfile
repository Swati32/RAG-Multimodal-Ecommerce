# Build context is the repo root (see AgentRuntimeArtifact.from_asset in
# infra/stacks/agents_stack.py) - needs src/ available to COPY.
FROM python:3.13-slim

WORKDIR /app

COPY src/ ./src/
RUN pip install --no-cache-dir boto3 bedrock-agentcore "opensearch-py>=2.6"

ENV PYTHONPATH=/app/src
EXPOSE 8080

CMD ["python", "-m", "agents.search_agent_runtime"]

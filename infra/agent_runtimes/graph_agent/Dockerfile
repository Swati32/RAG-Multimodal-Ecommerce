# Build context is the repo root (see AgentRuntimeArtifact.from_asset in
# infra/stacks/agents_stack.py) - needs src/ available to COPY.
FROM python:3.13-slim

WORKDIR /app

COPY src/ ./src/
RUN pip install --no-cache-dir boto3 bedrock-agentcore

ENV PYTHONPATH=/app/src
EXPOSE 8080

CMD ["python", "-m", "agents.graph_agent_runtime"]

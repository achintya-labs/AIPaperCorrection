# The only container that has the Claude CLI, and the only one that holds the Claude login.
# Build context is the repo root (it needs api/app for the runner).
FROM docker.io/library/python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
    && npm install -g @anthropic-ai/claude-code \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY claude/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY api/app ./app
# HOME is a persistent volume (compose: claude-home). The login lives in ~/.claude and ~/.claude.json,
# so the whole home is mounted. The CLI also refuses some modes as root.
RUN useradd -m -u 1000 claude
ENV HOME=/home/claude
USER claude
EXPOSE 9000
CMD ["uvicorn", "app.claude_runner:create_app", "--factory", "--host", "0.0.0.0", "--port", "9000"]

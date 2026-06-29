# Production image for the LiveKit interview agent worker.
# Runs `python agent.py start` and registers with LiveKit Cloud.
FROM python:3.12-slim

# Certificates for outbound TLS to LiveKit / OpenAI / Sarvam.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# uv (matches the project's uv.lock workflow).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Run as a non-root user.
RUN useradd -m -u 1000 appuser
USER appuser
WORKDIR /home/appuser/app

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH="/home/appuser/app/.venv/bin:$PATH"

# Install dependencies first for better layer caching.
COPY --chown=appuser pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

# Copy the app and finish the install.
COPY --chown=appuser . .
RUN uv sync --locked --no-dev

# Bake the Silero VAD + turn-detector model files into the image so the
# worker doesn't download them on first start.
RUN uv run python agent.py download-files

# Worker health/registration port used by LiveKit.
EXPOSE 8081

CMD ["uv", "run", "python", "agent.py", "start"]

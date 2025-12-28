FROM python:3.10-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install system dependencies if required (e.g. for pandas/numpy compilation sometimes)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-cache --no-install-project

COPY . .

ENV PATH="/app/.venv/bin:$PATH"

# Command is handled by docker-compose, but good to have a default
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.10-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install system dependencies if required (e.g. for pandas/numpy compilation sometimes)
# Added WeasyPrint dependencies: libcairo2, libpango-1.0-0, libpangocairo-1.0-0, libgdk-pixbuf2.0-0, shared-mime-info
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-cache --no-install-project

COPY . .

ENV PATH="/app/.venv/bin:$PATH"

# Command is handled by docker-compose, but good to have a default
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

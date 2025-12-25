FROM python:3.10-slim

WORKDIR /app

# Install system dependencies if required (e.g. for pandas/numpy compilation sometimes)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Command is handled by docker-compose, but good to have a default
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

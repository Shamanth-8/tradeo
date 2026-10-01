FROM python:3.12-slim

# curl for the healthcheck; build tooling for the few packages without wheels.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so code edits don't invalidate the install layer.
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend
COPY config /app/config

WORKDIR /app/backend

# The app writes the database, caches and the credential store at runtime.
RUN mkdir -p /app/database /app/data/cache /app/data/models

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BACKEND_HOST=0.0.0.0 \
    BACKEND_PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

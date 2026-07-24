# syntax=docker/dockerfile:1

# ---------- Stage 1: build dependencies with Poetry ----------
FROM python:3.11-slim AS builder

ENV POETRY_VERSION=1.8.3 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    PIP_NO_CACHE_DIR=1

RUN pip install "poetry==${POETRY_VERSION}"

WORKDIR /build
# Copy only dependency manifests first so this layer is cached and
# skipped on rebuilds unless pyproject.toml/poetry.lock actually change.
COPY pyproject.toml poetry.lock* ./
RUN poetry install --no-root --only main

# ---------- Stage 2: slim runtime image ----------
FROM python:3.11-slim AS runtime

# WeasyPrint (added in Module 8) needs native libs; installing them now
# keeps this Dockerfile stable across modules instead of editing it later.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/build/.venv/bin:$PATH"

WORKDIR /app
COPY --from=builder /build/.venv /build/.venv
COPY . .

EXPOSE 8000

# Railway/Render inject $PORT at runtime; default to 8000 for local `docker run`.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

# Build frontend
FROM node:20-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Build Python application
FROM python:3.11-slim

# Build arguments for version info
ARG GIT_BRANCH=unknown
ARG GIT_SHA=unknown

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    httpx>=0.27.0 \
    "fastapi>=0.115.0" \
    "uvicorn[standard]>=0.32.0" \
    pydantic>=2.0.0 \
    python-multipart>=0.0.9 \
    rapidfuzz>=3.0.0 \
    croniter>=2.0.0 \
    unidecode>=1.3.0

# Copy application code
COPY teamarr/ ./teamarr/
COPY app.py ./
COPY data/tsdb_seed.json ./data/

# Copy built frontend
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Write version file with build-time git info
RUN echo "${GIT_BRANCH}" > /app/.git-branch && \
    echo "${GIT_SHA}" > /app/.git-sha

# Create directory for data persistence
RUN mkdir -p /app/data/logs

# Expose the application port (same as V1)
EXPOSE 9195

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV GIT_BRANCH=${GIT_BRANCH}
ENV GIT_SHA=${GIT_SHA}

# Health check - start-period allows time for cache refresh (~20s)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9195/health').read()" || exit 1

# Run the application
CMD ["python", "app.py"]

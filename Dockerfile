FROM python:3.11-slim

# Build arguments for version info
ARG GIT_BRANCH=unknown
ARG GIT_SHA=unknown

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir \
    flask \
    requests \
    regex \
    croniter

# Copy application code
COPY api/ ./api/
COPY config/ ./config/
COPY database/ ./database/
COPY epg/ ./epg/
COPY static/ ./static/
COPY templates/ ./templates/
COPY utils/ ./utils/
COPY app.py .
COPY config.py .

# Write version file with build-time git info
RUN echo "${GIT_BRANCH}" > /app/.git-branch && \
    echo "${GIT_SHA}" > /app/.git-sha

# Create directory for data persistence
RUN mkdir -p /app/data

# Expose the application port
EXPOSE 9195

# Set environment variables
ENV FLASK_APP=app.py
ENV PYTHONUNBUFFERED=1
ENV GIT_BRANCH=${GIT_BRANCH}
ENV GIT_SHA=${GIT_SHA}

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9195/').read()" || exit 1

# Run the application
CMD ["python", "app.py"]

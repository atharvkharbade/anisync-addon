FROM python:3.12-slim

WORKDIR /app

# Install system dependencies (fonts-dejavu-core for scalable poster rendering)
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
RUN pip install uv --no-cache-dir

# Copy dependency spec first for layer caching
COPY pyproject.toml .

# Install dependencies
RUN uv pip install --system -r pyproject.toml

# Create non-root user and change ownership of app directories
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app

# Copy source code with correct ownership
COPY --chown=appuser:appuser . .

USER appuser

EXPOSE 5000

# Container healthcheck using standard Python urllib
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health', timeout=5)" || exit 1

CMD ["uvicorn", "run:app", "--host", "0.0.0.0", "--port", "5000", "--workers", "4"]


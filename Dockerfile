# ── Stage 1: build & download ─────────────────────────────────────────────────
# Install dependencies and pre-download the sentence-transformers model so the
# final image starts immediately without a network fetch at runtime.
FROM python:3.10-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# Create a virtual environment to keep the runtime image clean.
RUN python -m venv /venv

# Install the package and its dependencies from pyproject.toml.
COPY pyproject.toml .
COPY odm_search_mcp/ odm_search_mcp/
RUN /venv/bin/pip install --no-cache-dir --upgrade pip && \
    /venv/bin/pip install --no-cache-dir .

# Pre-download the embedding model (all-MiniLM-L6-v2, ~90 MB) into a
# predictable cache directory so it ships inside the image.
RUN HF_HOME=/model-cache /venv/bin/python -c \
    "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Generate the embeddings index at build time so the runtime image starts
# immediately without needing the files to exist in the source tree.
RUN HF_HOME=/model-cache /venv/bin/python -m odm_search_mcp.server --rebuild

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.10-slim AS runtime

WORKDIR /app

# Python packages from the build stage.
COPY --from=builder /venv /venv

# Pre-downloaded HuggingFace model cache.
COPY --from=builder /model-cache /model-cache

# Application source code.
COPY odm_search_mcp/ odm_search_mcp/

# Pre-computed embeddings index generated during the build stage (~4 MB).
# Re-build the image (or mount a volume) if you update the schema.
COPY --from=builder /build/embeddings/ embeddings/

ENV PATH="/venv/bin:$PATH" \
    HF_HOME=/model-cache \
    ODM_SCHEMA=odm_search_mcp/data/schemas/odm_v3.yaml \
    ODM_STORE=embeddings \
    ODM_MODEL=all-MiniLM-L6-v2 \
    ODM_HOST=0.0.0.0 \
    ODM_PORT=8000 \
    ODM_TRANSPORT=http

EXPOSE 8000

CMD ["python", "-m", "odm_search_mcp.server"]

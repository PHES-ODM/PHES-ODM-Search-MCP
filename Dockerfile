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
# Install the CPU-only build of PyTorch first. The default torch wheel bundles
# several GB of CUDA/nvidia libraries that are useless on a CPU-only server and
# can exhaust the disk during install ("No space left on device"). Pre-installing
# the CPU build satisfies the transitive torch requirement without them.
RUN /venv/bin/pip install --no-cache-dir --upgrade pip && \
    /venv/bin/pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    /venv/bin/pip install --no-cache-dir .

# Pre-download the embedding model (all-MiniLM-L6-v2, ~90 MB) into a
# predictable cache directory so it ships inside the image.
RUN HF_HOME=/model-cache /venv/bin/python -c \
    "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Generate the embeddings index at build time so the runtime image starts
# immediately without needing the files to exist in the source tree.
# ODM_BATCH_SIZE and the single-thread settings keep peak memory low so the
# build succeeds on constrained hosts (e.g. small EC2 instances) without OOM.
RUN HF_HOME=/model-cache ODM_BATCH_SIZE=8 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
    /venv/bin/python -m odm_search_mcp.server --rebuild

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

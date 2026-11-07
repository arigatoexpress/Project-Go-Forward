# ── Stage 1: Build Python dependencies ──
FROM python:3.12-slim AS python-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    libsm6 \
    libxext6 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install --user --no-cache-dir -r requirements.txt

# ── Stage 2: Build frontend ──
FROM node:20-slim AS frontend-builder

WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci

# listingRoutes.js imports repo-root CDN tables with a relative path that
# leaves frontend/ (../../../data/...). From this WORKDIR that resolves to
# /data/. The test job builds against a full checkout, so CI never hits a
# missing file; this stage only has frontend/ unless those tables are
# placed where the import looks. Copy every manufacturer CDN JSON, not one
# filename, so a new sibling (the unreachable-key list) does not fail the
# image build. tests/test_frontend_docker_data_imports.py fails CI when an
# import is not covered by this COPY.
COPY data/manufacturer_cdn_*.json /data/

COPY frontend/ ./
RUN npm run build

# ── Stage 3: Production image ──
FROM python:3.12-slim

# Install runtime dependencies for moviepy/opencv
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsm6 \
    libxext6 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser -d /app appuser

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=python-builder /root/.local /home/appuser/.local
ENV PATH=/home/appuser/.local/bin:$PATH
ENV PYTHONPATH=/home/appuser/.local/lib/python3.12/site-packages

# Copy application code
COPY . .

# Copy built frontend from frontend-builder
COPY --from=frontend-builder /frontend/dist ./frontend/dist

# Ensure output directories exist and are writable
RUN mkdir -p data/generated_docs data/generated_ads \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

CMD ["python", "main.py"]

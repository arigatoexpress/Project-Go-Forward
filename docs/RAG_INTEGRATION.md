# RAG over the regulatory document corpus

Semantic search + retrieval over the 63 PDFs in `tho_documents/`. Used to answer contextual questions ("which form is required for an over-55 community sale in Harris County?") with citations back to the source template and page.

## Why this exists

Keyword search over regulatory PDFs misses the nuance that's actually useful — phrasing varies between TMHA and TDHCA forms, and the useful answer often lives several paragraphs into a page. A retrieval-augmented pattern lets the agent cite the right form + page instead of guessing.

Inspired by [HKUDS/RAG-Anything](https://github.com/HKUDS/RAG-Anything) (trending on GitHub 2026-04-23). We deliberately do not use the `raganything` PyPI package in production: it brings in LightRAG, MinerU, Docling, and PaddleOCR — ~2 GB of dependencies for multimodal parsing our already-flat regulatory PDFs don't need. The interface in `tools/document_rag.py:DocumentRAG` is designed so a future swap to the full library is a drop-in replacement.

## Architecture

```
tho_documents/*.pdf
        │
        ▼
scripts/build_rag_index.py     (pypdf → chunk → Vertex AI text-embedding-004)
        │
        ▼
data/rag_index/
    metadata.json              (chunks with template/page/offset)
    embeddings.npy             (float32 matrix: n_chunks × 768)
        │
        ▼
tools/document_rag.DocumentRAG (lazy-loaded singleton in main.py)
        │
        ▼
POST /api/v1/rag/query         (THO_API_KEY auth, PII-safe responses)
```

### Dependencies — none new

- `pypdf` — already in requirements.txt (used by the document engine)
- `numpy` — already in requirements.txt
- `google-cloud-aiplatform` → `vertexai.language_models.TextEmbeddingModel` — already in requirements.txt (used by the agent)

Total image delta: **0 bytes**. The index file itself adds ~20 MB (see operations below).

### Scale notes

- 64 PDFs × ~3–30 pages × ~2–5 chunks/page ≈ **~3,000–10,000 chunks** on embedding
- 10k × 768 float32 = ~30 MB for `embeddings.npy` (worst case)
- Brute-force cosine over 10k vectors: <10 ms single-threaded; no FAISS / Milvus needed
- Vertex AI embedding cost: ~$0.00001 per 1k chars. Building once ≈ $0.10.
- Once the index is in place, queries are local CPU only — no per-query LLM cost unless the agent combines RAG with a completion call.

## Building the index

The index is **not committed to the repo** (`data/rag_index/` is gitignored). Rebuild when templates change.

```bash
# First-time setup: make sure Vertex AI credentials are available
gcloud auth application-default login
export GOOGLE_CLOUD_PROJECT=tho-ai-agent

# Full build
python scripts/build_rag_index.py

# Quick sanity check with just the first 5 PDFs
python scripts/build_rag_index.py --limit 5

# Dry-run: chunk everything, skip embedding calls (free)
python scripts/build_rag_index.py --dry-run
```

Outputs:
- `data/rag_index/metadata.json` — chunk metadata
- `data/rag_index/embeddings.npy` — float32 matrix

## Production deployment

The index has to exist inside the Cloud Run container for the endpoint to work. Three options:

1. **Build at image time** — add a step to `Dockerfile` after `COPY . .` that runs `python scripts/build_rag_index.py`. Slower builds and requires CI to have Vertex AI credentials, but the container is self-contained.
2. **Mount from GCS at startup** (recommended) — build the index locally or in CI, upload to `gs://tho-secure-documents/rag_index/`, and have the Cloud Run container download into `data/rag_index/` on cold start. Faster builds, index reusable across revisions.
3. **Skip in production for now** — the endpoint returns 503 with instructions. This is the default state when this branch first lands.

Choose one before merging to main.

## API

### `POST /api/v1/rag/query`

Auth: `Authorization: Bearer $THO_API_KEY` or `X-API-Key: $THO_API_KEY`.

Request:

```json
{
  "query": "Who is required to sign the Statement of Ownership?",
  "k": 5
}
```

- `query` (string, required, max 1000 chars) — the question.
- `k` (int, optional, default 5, max 20) — number of top results.

Response:

```json
{
  "query": "Who is required to sign the Statement of Ownership?",
  "count": 5,
  "results": [
    {
      "chunk_id": "TDHCA_1023-Statement-Ownership.pdf#p1#c2",
      "template": "TDHCA_1023-Statement-Ownership.pdf",
      "page": 1,
      "score": 0.8241,
      "text": "The Statement of Ownership must be signed by each record owner..."
    }
  ]
}
```

- `score` is a cosine similarity in `[-1, 1]`; typical good hits are `>= 0.7`.
- `text` is truncated to 500 chars for response size. Full chunk text is available from the index file.

Errors:
- `400` — `query` missing or non-JSON body.
- `401` — missing/invalid API key.
- `503` — RAG index not built on this instance. Run `build_rag_index.py`.
- `500` — upstream embedding or search failure.

## Future enhancements

| Idea | Why | Effort |
|-----|-----|-----|
| Hybrid search (BM25 + dense) | Better for exact-phrase queries like statute numbers | M |
| Per-category filters (TMHA vs TDHCA vs Internal) | Narrow results for specific workflow steps | S |
| Full raganything integration | Multimodal (images, tables, equations) for forms with diagrams | L — 2 GB dep cost |
| Re-rank with Gemini | Pick top-3 from top-20 via LLM judge | S |
| Stream citations into the chat agent | Customer-facing agent answers regulatory questions with sources | M |
| Streaming endpoint | For chunk-by-chunk UI rendering | S |

## Testing

```bash
# Unit tests, no network
python -m pytest tests/test_document_rag.py -v

# With the index built, smoke-test the endpoint locally
curl -X POST http://localhost:8080/api/v1/rag/query \
  -H "Authorization: Bearer $THO_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "arbitration agreement", "k": 3}'
```

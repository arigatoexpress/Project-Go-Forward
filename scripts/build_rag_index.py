#!/usr/bin/env python3
"""Build the RAG index for tho_documents/.

Usage
-----
    python scripts/build_rag_index.py
    python scripts/build_rag_index.py --limit 5          # first 5 PDFs only
    python scripts/build_rag_index.py --output data/rag_index
    python scripts/build_rag_index.py --dry-run          # chunk, don't embed

Prerequisites
-------------
    - Vertex AI enabled in GOOGLE_CLOUD_PROJECT, credentials available via
      ADC or gcloud auth application-default login.
    - pypdf, numpy, google-cloud-aiplatform already in requirements.txt.

Outputs
-------
    data/rag_index/metadata.json   (chunk metadata + embedding model info)
    data/rag_index/embeddings.npy  (float32 matrix: n_chunks x 768)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from pypdf import PdfReader

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.document_rag import VertexAIEmbeddingClient, chunk_text  # noqa: E402


def extract_chunks_from_pdf(pdf_path: Path) -> list[dict]:
    """Extract per-page, per-chunk records from a PDF."""
    try:
        reader = PdfReader(str(pdf_path))
    except Exception as e:
        print(f"  ! Failed to open {pdf_path.name}: {e}", file=sys.stderr)
        return []

    chunks: list[dict] = []
    for page_num, page in enumerate(reader.pages, start=1):
        try:
            raw = page.extract_text() or ""
        except Exception:
            continue
        text = raw.strip()
        if len(text) < 50:
            continue

        for chunk_idx, (offset, segment) in enumerate(chunk_text(text)):
            chunks.append(
                {
                    "id": f"{pdf_path.name}#p{page_num}#c{chunk_idx}",
                    "template": pdf_path.name,
                    "page": page_num,
                    "offset": offset,
                    "text": segment,
                }
            )
    return chunks


def batch_embed(
    client: VertexAIEmbeddingClient,
    chunks: list[dict],
    batch_size: int = 100,
) -> np.ndarray:
    """Embed all chunks in batches, printing progress."""
    total = len(chunks)
    buffers: list[np.ndarray] = []

    for start in range(0, total, batch_size):
        batch = chunks[start : start + batch_size]
        t0 = time.time()
        matrix = client.embed([c["text"] for c in batch])
        buffers.append(matrix)
        elapsed = time.time() - t0
        print(
            f"  embedded {min(start + batch_size, total)}/{total} "
            f"(+{len(batch)} in {elapsed:.1f}s)"
        )

    if not buffers:
        return np.zeros((0, 768), dtype=np.float32)
    return np.concatenate(buffers, axis=0).astype(np.float32, copy=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default=str(REPO_ROOT / "tho_documents"),
        help="Directory of PDFs to ingest (default: tho_documents/).",
    )
    parser.add_argument(
        "--output",
        default=str(REPO_ROOT / "data" / "rag_index"),
        help="Where to write metadata.json + embeddings.npy.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only process the first N PDFs (0 = all).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Chunk the corpus and print stats; skip embedding calls.",
    )
    args = parser.parse_args()

    source = Path(args.source)
    output = Path(args.output)
    if not source.is_dir():
        print(f"Source directory not found: {source}", file=sys.stderr)
        return 1

    pdf_paths = sorted(source.glob("*.pdf"))
    if args.limit > 0:
        pdf_paths = pdf_paths[: args.limit]

    if not pdf_paths:
        print(f"No PDFs found in {source}", file=sys.stderr)
        return 1

    print(f"Scanning {len(pdf_paths)} PDFs in {source}")

    all_chunks: list[dict] = []
    for pdf_path in pdf_paths:
        chunks = extract_chunks_from_pdf(pdf_path)
        all_chunks.extend(chunks)
        print(f"  + {pdf_path.name:<60} {len(chunks):>4} chunks")

    if not all_chunks:
        print("No chunks extracted. Aborting.", file=sys.stderr)
        return 2

    print(f"\nTotal: {len(all_chunks)} chunks across {len(pdf_paths)} PDFs")

    if args.dry_run:
        print("--dry-run: skipping embedding + write.")
        return 0

    print("\nEmbedding via Vertex AI text-embedding-004 ...")
    client = VertexAIEmbeddingClient()
    embeddings = batch_embed(client, all_chunks)
    print(f"Embedded matrix shape: {embeddings.shape}")

    output.mkdir(parents=True, exist_ok=True)
    metadata_path = output / "metadata.json"
    embeddings_path = output / "embeddings.npy"

    metadata = {
        "version": 1,
        "embedding_model": client.model_name,
        "embedding_dim": int(embeddings.shape[1]) if embeddings.size else 0,
        "chunk_count": len(all_chunks),
        "source_dir": str(source),
        "chunks": all_chunks,
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False))
    np.save(embeddings_path, embeddings)

    print(f"\nWrote {metadata_path}")
    print(f"Wrote {embeddings_path}")
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

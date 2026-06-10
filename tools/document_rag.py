"""Minimal RAG layer over the THO regulatory document corpus.

Design notes
------------
- Zero new dependencies: pypdf (already present), numpy (already present),
  vertexai embeddings via google-cloud-aiplatform (already present).
- Pre-built index on disk: metadata.json + embeddings.npy. Rebuild with
  scripts/build_rag_index.py.
- Search: brute-force cosine over the full matrix. At O(10^4) chunks this
  is <10ms per query on a single core; no vector DB required.
- Inspired by HKUDS/RAG-Anything (trending 2026-04-23); we deliberately
  avoid the full raganything package because its MinerU / Docling / LightRAG
  dependency tree adds ~2 GB to the Docker image for a single-digit-MB
  index that our own PDFs don't need advanced multimodal parsing for.
- To upgrade to raganything later, swap the VertexAIEmbeddingClient and
  the query() function behind the same DocumentRAG interface.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np


class EmbeddingClient(Protocol):
    """Minimal contract for any embedding backend."""

    def embed(self, texts: list[str]) -> np.ndarray:
        """Return an (n, d) float32 array."""
        ...


@dataclass
class RetrievalResult:
    chunk_id: str
    template: str
    page: int
    text: str
    score: float

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "template": self.template,
            "page": self.page,
            "text": self.text,
            "score": self.score,
        }


class VertexAIEmbeddingClient:
    """Default embedding client using Vertex AI text-embedding-004."""

    def __init__(self, model_name: str = "text-embedding-004"):
        from vertexai.language_models import TextEmbeddingModel

        self._model = TextEmbeddingModel.from_pretrained(model_name)
        self.model_name = model_name

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 768), dtype=np.float32)
        embeddings = self._model.get_embeddings(texts)
        return np.asarray([e.values for e in embeddings], dtype=np.float32)


class DocumentRAG:
    """Query the pre-built RAG index.

    Usage
    -----
    >>> rag = DocumentRAG()
    >>> rag.load()
    >>> results = rag.query("Who signs the sales contract?", k=5)
    >>> for r in results:
    ...     print(r.template, r.page, r.score)
    """

    def __init__(
        self,
        index_dir: str | Path = "data/rag_index",
        embedding_client: EmbeddingClient | None = None,
    ):
        self.index_dir = Path(index_dir)
        self._embedding_client = embedding_client
        self._chunks: list[dict] | None = None
        self._embeddings: np.ndarray | None = None
        self._metadata: dict | None = None

    @property
    def embedding_client(self) -> EmbeddingClient:
        if self._embedding_client is None:
            self._embedding_client = VertexAIEmbeddingClient()
        return self._embedding_client

    @property
    def is_loaded(self) -> bool:
        return self._chunks is not None and self._embeddings is not None

    def load(self) -> None:
        metadata_path = self.index_dir / "metadata.json"
        embeddings_path = self.index_dir / "embeddings.npy"
        if not metadata_path.exists() or not embeddings_path.exists():
            raise FileNotFoundError(
                f"RAG index not built. Expected {metadata_path} and {embeddings_path}. "
                f"Run: python scripts/build_rag_index.py"
            )

        metadata = json.loads(metadata_path.read_text())
        embeddings = np.load(embeddings_path)

        chunk_count = len(metadata.get("chunks", []))
        if chunk_count != embeddings.shape[0]:
            raise ValueError(
                f"Index mismatch: {chunk_count} chunks in metadata, "
                f"{embeddings.shape[0]} rows in embeddings.npy"
            )

        self._metadata = metadata
        self._chunks = metadata["chunks"]
        self._embeddings = embeddings.astype(np.float32, copy=False)

    def query(self, question: str, k: int = 5) -> list[RetrievalResult]:
        if not self.is_loaded:
            self.load()
        if not question or not question.strip():
            return []

        k = max(1, min(k, len(self._chunks)))
        query_vec = self.embedding_client.embed([question])[0]
        return self._rank_by_cosine(query_vec, k)

    def _rank_by_cosine(self, query_vec: np.ndarray, k: int) -> list[RetrievalResult]:
        assert self._embeddings is not None and self._chunks is not None

        matrix_norms = np.linalg.norm(self._embeddings, axis=1)
        query_norm = np.linalg.norm(query_vec)
        denom = matrix_norms * query_norm
        denom[denom == 0] = 1.0  # avoid div-by-zero

        similarities = self._embeddings @ query_vec / denom
        top_idx = np.argsort(similarities)[-k:][::-1]

        return [
            RetrievalResult(
                chunk_id=self._chunks[i]["id"],
                template=self._chunks[i]["template"],
                page=self._chunks[i]["page"],
                text=self._chunks[i]["text"],
                score=float(similarities[i]),
            )
            for i in top_idx
        ]


def chunk_text(
    text: str,
    chunk_size: int = 500,
    overlap: int = 100,
    min_chunk_chars: int = 50,
) -> Iterable[tuple[int, str]]:
    """Yield (start_offset, chunk_text) pairs. Filters out too-short chunks."""
    if not text or len(text) < min_chunk_chars:
        return
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    step = chunk_size - overlap
    for start in range(0, len(text), step):
        segment = text[start : start + chunk_size].strip()
        if len(segment) >= min_chunk_chars:
            yield start, segment

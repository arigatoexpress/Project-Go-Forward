"""Unit tests for tools/document_rag.py.

These tests avoid any Vertex AI network calls by injecting a fake embedding
client. The endpoint tests in test_api_v1.py exercise auth / rate limit / PII
redaction — here we focus on the chunking and similarity math.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.document_rag import (  # noqa: E402
    DocumentRAG,
    RetrievalResult,
    chunk_text,
)


class FakeEmbeddingClient:
    """Deterministic embedding client backed by a tiny vocab→vector map."""

    def __init__(self, vocab_vectors: dict[str, np.ndarray], dim: int = 3):
        self.dim = dim
        self.vocab_vectors = vocab_vectors

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            words = text.lower().split()
            vec = np.zeros(self.dim, dtype=np.float32)
            for w in words:
                if w in self.vocab_vectors:
                    vec = vec + self.vocab_vectors[w]
            out[i] = vec
        return out


# ─── chunk_text ─────────────────────────────────────────────────────────────


def test_chunk_text_short_input_yields_nothing():
    assert list(chunk_text("abc", chunk_size=100, overlap=10)) == []


def test_chunk_text_overlap_must_be_smaller_than_size():
    with pytest.raises(ValueError):
        list(chunk_text("a" * 200, chunk_size=50, overlap=50))


def test_chunk_text_produces_overlapping_windows():
    text = "abcdefghijklmnopqrstuvwxyz" * 30  # 780 chars
    chunks = list(chunk_text(text, chunk_size=100, overlap=20, min_chunk_chars=50))
    assert len(chunks) > 1
    # First chunk starts at offset 0
    assert chunks[0][0] == 0
    # Windows overlap by chunk_size - overlap
    second_start = chunks[1][0]
    assert second_start == 80


def test_chunk_text_filters_short_segments():
    chunks = list(chunk_text("short segment that is ok", chunk_size=30, overlap=5))
    # Only segments with >= min_chunk_chars (default 50) survive
    assert chunks == []


# ─── DocumentRAG.query ──────────────────────────────────────────────────────


def _build_fake_index(tmp_path: Path, chunks: list[dict], embeddings: np.ndarray):
    index_dir = tmp_path / "rag_index"
    index_dir.mkdir()
    metadata = {
        "version": 1,
        "embedding_model": "fake",
        "embedding_dim": int(embeddings.shape[1]),
        "chunk_count": len(chunks),
        "chunks": chunks,
    }
    (index_dir / "metadata.json").write_text(json.dumps(metadata))
    np.save(index_dir / "embeddings.npy", embeddings.astype(np.float32))
    return index_dir


def test_load_raises_when_index_missing(tmp_path):
    rag = DocumentRAG(index_dir=tmp_path / "does-not-exist")
    with pytest.raises(FileNotFoundError):
        rag.load()


def test_load_raises_on_size_mismatch(tmp_path):
    chunks = [{"id": "a", "template": "x.pdf", "page": 1, "text": "alpha"}]
    bad_embeddings = np.zeros((2, 3), dtype=np.float32)  # 2 rows for 1 chunk
    index_dir = _build_fake_index(tmp_path, chunks, bad_embeddings)

    rag = DocumentRAG(index_dir=index_dir)
    with pytest.raises(ValueError):
        rag.load()


def test_query_ranks_by_cosine_similarity(tmp_path):
    # Vocab: three orthogonal "concepts"
    vocab = {
        "contract": np.array([1.0, 0.0, 0.0], dtype=np.float32),
        "warranty": np.array([0.0, 1.0, 0.0], dtype=np.float32),
        "installation": np.array([0.0, 0.0, 1.0], dtype=np.float32),
    }
    client = FakeEmbeddingClient(vocab, dim=3)

    chunks = [
        {"id": "a", "template": "contract.pdf", "page": 1, "text": "contract"},
        {"id": "b", "template": "warranty.pdf", "page": 1, "text": "warranty"},
        {"id": "c", "template": "install.pdf", "page": 1, "text": "installation"},
    ]
    embeddings = client.embed([c["text"] for c in chunks])
    index_dir = _build_fake_index(tmp_path, chunks, embeddings)

    rag = DocumentRAG(index_dir=index_dir, embedding_client=client)
    results = rag.query("contract", k=3)

    assert [r.chunk_id for r in results] == ["a", "b", "c"] or results[0].chunk_id == "a"
    assert results[0].template == "contract.pdf"
    assert results[0].score > results[1].score
    assert results[0].score > 0.99  # exact match


def test_query_returns_empty_for_blank_question(tmp_path):
    chunks = [{"id": "a", "template": "x.pdf", "page": 1, "text": "alpha"}]
    embeddings = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
    index_dir = _build_fake_index(tmp_path, chunks, embeddings)

    client = FakeEmbeddingClient({"alpha": np.array([1.0, 0.0, 0.0], dtype=np.float32)})
    rag = DocumentRAG(index_dir=index_dir, embedding_client=client)

    assert rag.query("", k=3) == []
    assert rag.query("   ", k=3) == []


def test_query_caps_k_to_chunk_count(tmp_path):
    chunks = [
        {"id": "a", "template": "x.pdf", "page": 1, "text": "alpha"},
        {"id": "b", "template": "x.pdf", "page": 2, "text": "beta"},
    ]
    embeddings = np.array(
        [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32
    )
    index_dir = _build_fake_index(tmp_path, chunks, embeddings)

    client = FakeEmbeddingClient({"alpha": np.array([1.0, 0.0, 0.0], dtype=np.float32)})
    rag = DocumentRAG(index_dir=index_dir, embedding_client=client)

    # Ask for more than exist; should return all
    results = rag.query("alpha", k=99)
    assert len(results) == 2


def test_retrieval_result_serializes(tmp_path):
    result = RetrievalResult(
        chunk_id="x#p1#c0",
        template="x.pdf",
        page=1,
        text="hello",
        score=0.5,
    )
    d = result.to_dict()
    assert d["chunk_id"] == "x#p1#c0"
    assert d["page"] == 1
    assert d["score"] == 0.5

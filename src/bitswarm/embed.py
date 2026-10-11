"""Embedders. 'hash' = deterministic offline (tests/CI). 'bge' = bge-small-en-v1.5 via fastembed."""
from __future__ import annotations
import hashlib, re
import numpy as np
from .common import sha256_bytes

_TOK = re.compile(r"[a-z0-9]+")


class HashEmbedder:
    """Deterministic hashed bag of words + bigrams. Not semantic; lets the whole pipeline
    run offline with zero downloads. Swap for BGE for real quality."""
    name = "hash-v1"
    dim = 384
    dtype = "float16"
    query_prefix = ""
    refusal_cutoff = 0.12   # calibrate on your eval set

    def __init__(self):
        self.model_sha256 = sha256_bytes(f"{self.name}:{self.dim}".encode())

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        toks = _TOK.findall(text.lower())
        feats = toks + [a + "_" + b for a, b in zip(toks, toks[1:])]
        for f in feats:
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
            v[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.stack([self._vec(t) for t in texts]) if texts else np.zeros((0, self.dim), np.float32)

    def embed_query(self, q: str) -> np.ndarray:
        return self.embed([self.query_prefix + q])[0]


class BgeEmbedder:
    name = "BAAI/bge-small-en-v1.5"
    dim = 384
    dtype = "float16"
    query_prefix = "Represent this sentence for searching relevant passages: "
    refusal_cutoff = 0.45   # starting guess. calibrate on your eval set

    def __init__(self):
        from fastembed import TextEmbedding   # lazy import
        self._m = TextEmbedding(model_name=self.name)
        self.model_sha256 = self._model_hash()

    def _model_hash(self) -> str:
        # hash the ONNX file if we can find it, else fall back to a name-based id
        try:
            from pathlib import Path
            import os
            cache = Path(os.environ.get("FASTEMBED_CACHE_PATH", "/tmp/fastembed_cache"))
            for p in cache.rglob("model_optimized.onnx"):
                from .common import sha256_file
                return sha256_file(p)
            for p in cache.rglob("*.onnx"):
                from .common import sha256_file
                return sha256_file(p)
        except Exception:
            pass
        return sha256_bytes(self.name.encode())

    def embed(self, texts: list[str]) -> np.ndarray:
        arr = np.array(list(self._m.embed(texts)), dtype=np.float32)
        n = np.linalg.norm(arr, axis=1, keepdims=True)
        return arr / np.where(n == 0, 1, n)

    def embed_query(self, q: str) -> np.ndarray:
        return self.embed([self.query_prefix + q])[0]


def get_embedder(kind: str):
    if kind == "bge":
        return BgeEmbedder()
    return HashEmbedder()


def fingerprint(e) -> dict:
    return {"model": e.name, "model_sha256": e.model_sha256, "dim": e.dim,
            "dtype": e.dtype, "normalized": True, "query_prefix": e.query_prefix}

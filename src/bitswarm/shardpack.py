"""Build an immutable, signed shard: chunks.parquet + vectors.parquet + manifest.json + manifest.sig."""
from __future__ import annotations
import json, shutil, tempfile
from pathlib import Path
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from .common import (SCHEMA_VERSION, canonical, now, sha256_bytes, sha256_file, ALLOWED_SHARD_FILES)
from .embed import fingerprint as embed_fp
from .identity import Identity

CHUNK_SCHEMA = pa.schema([
    ("chunk_id", pa.string()), ("doc_id", pa.string()), ("doc_title", pa.string()),
    ("source_ref", pa.string()), ("chunk_index", pa.int32()), ("char_start", pa.int64()),
    ("char_end", pa.int64()), ("section", pa.string()), ("text", pa.string()),
])
VECTOR_SCHEMA = pa.schema([("chunk_id", pa.string()), ("vector", pa.binary())])  # float16 LE bytes

BM25_TOKENIZER = "sqlite-fts5-porter-unicode61"


def compute_shard_id(files: list[dict]) -> str:
    joined = "\n".join(f"{f['path']}:{f['sha256']}" for f in sorted(files, key=lambda x: x["path"]))
    return sha256_bytes(joined.encode())


def write_parquet(out: Path, recs: list[dict], vecs: np.ndarray):
    cols = {k: [r[k] for r in recs] for k in CHUNK_SCHEMA.names}
    pq.write_table(pa.table(cols, schema=CHUNK_SCHEMA), out / "chunks.parquet", compression="zstd")
    blobs = [v.astype("<f2").tobytes() for v in vecs]
    pq.write_table(pa.table({"chunk_id": [r["chunk_id"] for r in recs], "vector": blobs},
                            schema=VECTOR_SCHEMA), out / "vectors.parquet", compression="zstd")


def pack(seed_dir: Path, identity: Identity, recs: list[dict], vecs: np.ndarray, embedder, *,
         title: str, description: str = "", tags: list | None = None, language: str = "en",
         expires_at: int | None = None, supersedes: str | None = None,
         redact_paths: bool = False, chunk_target: int = 400, chunk_overlap: float = 0.12) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="pack_"))
    try:
        write_parquet(tmp, recs, vecs)
        files = [{"path": n, "size": (tmp / n).stat().st_size, "sha256": sha256_file(tmp / n)}
                 for n in ALLOWED_SHARD_FILES]
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "shard_id": compute_shard_id(files),
            "supersedes": supersedes,
            "title": title, "description": description, "tags": tags or [], "language": language,
            "publisher": {"pubkey": identity.pubkey, "name": identity.name},
            "created_at": now(), "expires_at": expires_at,
            "embedding": embed_fp(embedder),
            "chunking": {"strategy": "paragraph-snapped-word-window", "target": chunk_target,
                         "overlap": chunk_overlap, "tokenizer": "whitespace"},
            "bm25_tokenizer": BM25_TOKENIZER,
            "counts": {"documents": len({r["doc_id"] for r in recs}), "chunks": len(recs)},
            "files": files,
            "torrent": None,
            "encryption": "none",
            "privacy": {"paths_redacted": redact_paths},
        }
        (tmp / "manifest.json").write_bytes(canonical(manifest))
        (tmp / "manifest.sig").write_text(identity.sign(canonical(manifest)))
        dest = seed_dir / manifest["shard_id"]
        if dest.exists():
            shutil.rmtree(dest)
        seed_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp), str(dest))
        return dest
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def read_manifest(shard_dir: Path) -> tuple[dict, str]:
    m = json.loads((shard_dir / "manifest.json").read_text())
    sig = (shard_dir / "manifest.sig").read_text().strip() if (shard_dir / "manifest.sig").exists() else ""
    return m, sig

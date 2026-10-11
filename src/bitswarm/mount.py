"""Mount pipeline = security gate. Every shard from a peer passes steps 1-8 in order.
Any failure raises MountError(step) and is written to the audit log."""
from __future__ import annotations
import random, shutil, time
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np
import pyarrow.parquet as pq
from .common import (SCHEMA_VERSION, ALLOWED_SHARD_FILES, canonical, now, sha256_file, Home)
from .embed import fingerprint as embed_fp
from .identity import TrustList, verify, fingerprint as key_fp
from .indexer import embed_text
from .shardpack import CHUNK_SCHEMA, VECTOR_SCHEMA, compute_shard_id, read_manifest
from .store import Store

STEPS = {1: "file hashes", 2: "manifest signature", 3: "trusted publisher", 4: "embedding fingerprint",
         5: "schema validation", 6: "sample re-embedding", 7: "expiry & quota", 8: "import"}


class MountError(Exception):
    def __init__(self, step: int, reason: str):
        super().__init__(f"step {step} ({STEPS[step]}): {reason}")
        self.step, self.reason = step, reason


@dataclass
class MountResult:
    shard_id: str
    chunks: int
    steps: list = field(default_factory=list)   # [{step,name,ok,ms}]


def check_manifest_light(manifest: dict, sig: str, trust: TrustList, embedder, cfg, now_ts: int | None = None):
    """Steps 2,3,4 + expiry/size. Usable BEFORE downloading (sync pre-accept)."""
    now_ts = now_ts or now()
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise MountError(2, f"unknown schema_version {manifest.get('schema_version')}")
    pub = (manifest.get("publisher") or {}).get("pubkey", "")
    if not sig or not verify(pub, canonical(manifest), sig):
        raise MountError(2, "signature missing or invalid")
    if not trust.is_trusted(pub):
        raise MountError(3, f"publisher {key_fp(pub)} not in trust list")
    fp, mine = manifest.get("embedding", {}), embed_fp(embedder)
    for k in ("model", "model_sha256", "dim", "dtype"):
        if fp.get(k) != mine[k]:
            raise MountError(4, f"embedding {k} mismatch: shard={fp.get(k)!r} local={mine[k]!r}")
    exp = manifest.get("expires_at")
    if exp is not None and exp <= now_ts:
        raise MountError(7, "shard expired")
    total = sum(f.get("size", 0) for f in manifest.get("files", []))
    if total > cfg.max_shard_bytes:
        raise MountError(7, f"shard too large ({total} > {cfg.max_shard_bytes})")


def _dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def mount(home: Home, shard_dir: Path, embedder, store: Store, trust: TrustList, cfg,
          progress=None, adopt: bool = True, rng: random.Random | None = None) -> MountResult:
    rng = rng or random.Random()
    res = MountResult(shard_id="?", chunks=0)

    def run(step, fn):
        t = time.time()
        try:
            fn()
        except MountError:
            res.steps.append({"step": step, "name": STEPS[step], "ok": False})
            if progress: progress(res.steps[-1])
            raise
        except Exception as e:                       # any parse blow-up = schema rejection, never a crash
            res.steps.append({"step": step, "name": STEPS[step], "ok": False})
            if progress: progress(res.steps[-1])
            raise MountError(step if step != 8 else 8, f"{type(e).__name__}: {e}")
        res.steps.append({"step": step, "name": STEPS[step], "ok": True, "ms": int((time.time() - t) * 1000)})
        if progress: progress(res.steps[-1])

    ctx: dict = {}
    try:
        try:
            manifest, sig = read_manifest(shard_dir)
        except Exception as e:
            raise MountError(2, f"manifest unreadable: {e}")
        ctx["m"], ctx["sig"] = manifest, sig
        res.shard_id = manifest.get("shard_id", "?")

        def s1():
            files = manifest.get("files")
            if not isinstance(files, list) or {f.get("path") for f in files} != set(ALLOWED_SHARD_FILES):
                raise MountError(1, "manifest file list invalid (must be exactly chunks+vectors parquet)")
            extra = {p.name for p in shard_dir.iterdir()} - set(ALLOWED_SHARD_FILES) - {"manifest.json", "manifest.sig"}
            if extra:
                raise MountError(1, f"unexpected files in shard: {sorted(extra)}")
            for f in files:
                p = shard_dir / f["path"]
                if not p.is_file() or p.stat().st_size != f["size"] or sha256_file(p) != f["sha256"]:
                    raise MountError(1, f"hash/size mismatch for {f['path']}")
            if compute_shard_id(files) != manifest.get("shard_id"):
                raise MountError(1, "shard_id does not match file hashes")
        run(1, s1)

        run(2, lambda: _sig_only(manifest, sig))
        run(3, lambda: _trust_only(manifest, trust))
        run(4, lambda: _fp_only(manifest, embedder))

        def s5():
            ct = pq.read_table(shard_dir / "chunks.parquet")
            vt = pq.read_table(shard_dir / "vectors.parquet")
            if ct.schema != CHUNK_SCHEMA or vt.schema != VECTOR_SCHEMA:
                raise MountError(5, "unexpected columns/types")
            n = manifest["counts"]["chunks"]
            if ct.num_rows != n or vt.num_rows != n:
                raise MountError(5, "row count does not match manifest")
            rows = ct.to_pylist()
            vecs = vt.column("vector").to_pylist()
            ids_c = [r["chunk_id"] for r in rows]
            if len(set(ids_c)) != n or ids_c != vt.column("chunk_id").to_pylist():
                raise MountError(5, "chunk ids duplicated or not aligned with vectors")
            want = embedder.dim * 2
            for r, v in zip(rows, vecs):
                if len(r["text"]) > cfg.max_chunk_chars:
                    raise MountError(5, "chunk text exceeds cap")
                if len(v) != want:
                    raise MountError(5, "vector byte length wrong")
            ctx["rows"], ctx["vecs"] = rows, vecs
        run(5, s5)

        def s6():
            rows, vecs = ctx["rows"], ctx["vecs"]
            idx = rng.sample(range(len(rows)), min(20, len(rows)))
            fresh = embedder.embed([embed_text(rows[i]["doc_title"], rows[i]["section"], rows[i]["text"]) for i in idx])
            for j, i in enumerate(idx):
                shipped = np.frombuffer(vecs[i], dtype="<f2").astype(np.float32)
                cos = float(shipped @ fresh[j] / ((np.linalg.norm(shipped) * np.linalg.norm(fresh[j])) or 1))
                if cos < 0.99:
                    raise MountError(6, f"re-embedding cosine {cos:.3f} < 0.99 on chunk {rows[i]['chunk_id'][:8]}")
        run(6, s6)

        def s7():
            exp = manifest.get("expires_at")
            if exp is not None and exp <= now():
                raise MountError(7, "shard expired")
            if _dir_size(home.seed_dir) + _dir_size(shard_dir) > cfg.quota_bytes:
                raise MountError(7, "disk quota exceeded")
            if sum(f["size"] for f in manifest["files"]) > cfg.max_shard_bytes:
                raise MountError(7, "shard exceeds per-shard cap")
        run(7, s7)

        def s8():
            store.import_shard(manifest, ctx["rows"], ctx["vecs"])
            if adopt:
                dest = home.seed_dir / manifest["shard_id"]
                if shard_dir.resolve() != dest.resolve():
                    if dest.exists():
                        shutil.rmtree(dest)
                    home.seed_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(shard_dir), str(dest))
        run(8, s8)
        res.chunks = manifest["counts"]["chunks"]
        home.audit("mount_ok", shard_id=res.shard_id, chunks=res.chunks, publisher=key_fp(manifest["publisher"]["pubkey"]))
        return res
    except MountError as e:
        home.audit("mount_rejected", shard_id=res.shard_id, step=e.step, step_name=STEPS[e.step], reason=e.reason)
        e.result = res
        raise


# small single-purpose helpers so each gate step is individually attributable
def _sig_only(manifest, sig):
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise MountError(2, f"unknown schema_version {manifest.get('schema_version')}")
    pub = (manifest.get("publisher") or {}).get("pubkey", "")
    if not sig or not verify(pub, canonical(manifest), sig):
        raise MountError(2, "signature missing or invalid")


def _trust_only(manifest, trust):
    pub = manifest["publisher"]["pubkey"]
    if not trust.is_trusted(pub):
        raise MountError(3, f"publisher {key_fp(pub)} not in trust list")


def _fp_only(manifest, embedder):
    fp, mine = manifest.get("embedding", {}), embed_fp(embedder)
    for k in ("model", "model_sha256", "dim", "dtype"):
        if fp.get(k) != mine[k]:
            raise MountError(4, f"embedding {k} mismatch: shard={fp.get(k)!r} local={mine[k]!r}")

"""Query store: one local SQLite DB = vectors + FTS5/BM25 over ALL mounted shards (global BM25 stats)."""
from __future__ import annotations
import json, re, sqlite3, threading
from pathlib import Path
import numpy as np

_INJ = re.compile(
    r"(ignore (all |any )?(previous|prior|above) (instructions|rules)|disregard (the )?(system|previous)|"
    r"you are now|system prompt|new instructions:|reveal (your|the) (prompt|instructions)|"
    r"</?(system|assistant|user)>|\[/?INST\]|<\|[a-z_]+\|>)", re.I)
_CTRL = re.compile(r"(<\|[a-z_]+\|>|\[/?INST\]|<<SYS>>|<</SYS>>)", re.I)


def looks_injected(text: str) -> bool:
    return bool(_INJ.search(text))


def strip_control_tokens(text: str) -> str:
    return _CTRL.sub("", text)


def _fts_query(q: str) -> str:
    toks = re.findall(r"[A-Za-z0-9]+", q.lower())
    return " OR ".join(f'"{t}"' for t in toks[:32])


class Store:
    def __init__(self, store_dir: Path, dim: int = 384):
        store_dir.mkdir(parents=True, exist_ok=True)
        self.dim = dim
        self.db = sqlite3.connect(store_dir / "store.db", check_same_thread=False)
        self.lock = threading.RLock()
        self._cache = None
        with self.lock:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS shards(shard_id TEXT PRIMARY KEY, title TEXT, publisher TEXT,
                publisher_name TEXT, mounted_at INTEGER, expires_at INTEGER, chunks INTEGER, manifest TEXT);
            CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, chunk_id TEXT, shard_id TEXT,
                doc_id TEXT, doc_title TEXT, source_ref TEXT, chunk_index INTEGER, section TEXT,
                text TEXT, suspicious INTEGER, vec BLOB, UNIQUE(chunk_id, shard_id));
            CREATE INDEX IF NOT EXISTS chunks_shard ON chunks(shard_id);
            CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(text, doc_title, tokenize='porter unicode61');
            """)

    # ---- import / unmount -------------------------------------------------
    def import_shard(self, manifest: dict, rows: list[dict], vecs16: list[bytes]):
        with self.lock, self.db:
            self.db.execute("DELETE FROM chunks WHERE shard_id=?", (manifest["shard_id"],))
            for r, vb in zip(rows, vecs16):
                text = strip_control_tokens(r["text"])
                cur = self.db.execute(
                    "INSERT OR REPLACE INTO chunks(chunk_id,shard_id,doc_id,doc_title,source_ref,chunk_index,"
                    "section,text,suspicious,vec) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (r["chunk_id"], manifest["shard_id"], r["doc_id"], r["doc_title"], r["source_ref"],
                     r["chunk_index"], r["section"], text, int(looks_injected(text)), vb))
                self.db.execute("INSERT INTO fts(rowid,text,doc_title) VALUES(?,?,?)",
                                (cur.lastrowid, text, r["doc_title"]))
            pub = manifest["publisher"]
            self.db.execute("INSERT OR REPLACE INTO shards VALUES(?,?,?,?,?,?,?,?)",
                            (manifest["shard_id"], manifest["title"], pub["pubkey"], pub["name"],
                             int(__import__("time").time()), manifest.get("expires_at"),
                             len(rows), json.dumps(manifest)))
        self._cache = None

    def unmount(self, shard_id: str):
        with self.lock, self.db:
            ids = [r[0] for r in self.db.execute("SELECT id FROM chunks WHERE shard_id=?", (shard_id,))]
            self.db.executemany("DELETE FROM fts WHERE rowid=?", [(i,) for i in ids])
            self.db.execute("DELETE FROM chunks WHERE shard_id=?", (shard_id,))
            self.db.execute("DELETE FROM shards WHERE shard_id=?", (shard_id,))
        self._cache = None

    def mounted_ids(self) -> set:
        with self.lock:
            return {r[0] for r in self.db.execute("SELECT shard_id FROM shards")}

    def shards(self) -> list[dict]:
        with self.lock:
            cur = self.db.execute("SELECT shard_id,title,publisher,publisher_name,mounted_at,expires_at,chunks FROM shards")
            keys = ["shard_id", "title", "publisher", "publisher_name", "mounted_at", "expires_at", "chunks"]
            return [dict(zip(keys, r)) for r in cur]

    def expired(self, now_ts: int) -> list[str]:
        with self.lock:
            return [r[0] for r in self.db.execute(
                "SELECT shard_id FROM shards WHERE expires_at IS NOT NULL AND expires_at<=?", (now_ts,))]

    def total_chunks(self) -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    # ---- search ------------------------------------------------------------
    def _matrix(self):
        if self._cache is None:
            with self.lock:
                rows = self.db.execute("SELECT id, vec FROM chunks ORDER BY id").fetchall()
            ids = np.array([r[0] for r in rows], dtype=np.int64)
            mat = (np.stack([np.frombuffer(r[1], dtype="<f2") for r in rows]).astype(np.float32)
                   if rows else np.zeros((0, self.dim), np.float32))
            self._cache = (ids, mat)
        return self._cache

    def dense(self, qvec: np.ndarray, k: int = 50) -> list[tuple[int, float]]:
        ids, mat = self._matrix()
        if not len(ids):
            return []
        sims = mat @ qvec.astype(np.float32)
        top = np.argsort(-sims)[:k]
        return [(int(ids[i]), float(sims[i])) for i in top]

    def bm25(self, query: str, k: int = 50) -> list[tuple[int, float]]:
        q = _fts_query(query)
        if not q:
            return []
        with self.lock:
            try:
                cur = self.db.execute(
                    "SELECT rowid, bm25(fts) FROM fts WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT ?", (q, k))
                return [(r[0], -r[1]) for r in cur]
            except sqlite3.OperationalError:
                return []

    def get(self, rid: int) -> dict:
        with self.lock:
            r = self.db.execute(
                "SELECT c.id,c.chunk_id,c.shard_id,c.doc_id,c.doc_title,c.source_ref,c.chunk_index,c.section,"
                "c.text,c.suspicious,s.publisher,s.publisher_name,s.title FROM chunks c "
                "JOIN shards s ON s.shard_id=c.shard_id WHERE c.id=?", (rid,)).fetchone()
        keys = ["id", "chunk_id", "shard_id", "doc_id", "doc_title", "source_ref", "chunk_index", "section",
                "text", "suspicious", "publisher", "publisher_name", "shard_title"]
        return dict(zip(keys, r)) if r else {}

    def get_by_chunk_id(self, chunk_id: str) -> dict:
        with self.lock:
            r = self.db.execute("SELECT id FROM chunks WHERE chunk_id=? LIMIT 1", (chunk_id,)).fetchone()
        return self.get(r[0]) if r else {}


def rrf(rank_lists: list[list[int]], k: int = 60) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion: score = sum 1/(k+rank). Uses ranks only."""
    scores: dict[int, float] = {}
    for lst in rank_lists:
        for rank, rid in enumerate(lst, start=1):
            scores[rid] = scores.get(rid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: -x[1])

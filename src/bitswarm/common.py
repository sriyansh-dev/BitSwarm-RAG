"""Shared helpers: node home layout, canonical JSON, hashing, config."""
from __future__ import annotations
import hashlib, json, os, time
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1
ALLOWED_SHARD_FILES = ("chunks.parquet", "vectors.parquet")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def canonical(obj) -> bytes:
    """Deterministic JSON: sorted keys, fixed separators. Signatures cover this."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def now() -> int:
    return int(time.time())


@dataclass
class Config:
    team_name: str = "team"
    team_secret: str = "change-me"
    mcast_group: str = "239.255.49.50"
    mcast_port: int = 49500
    api_port: int = 49501
    bt_port: int = 49502
    ui_port: int = 8080
    bind: str = "127.0.0.1"          # set to 0.0.0.0 to serve LAN
    embedder: str = "hash"            # "hash" (offline test) or "bge" (fastembed)
    manual_peers: list = field(default_factory=list)   # ["ip:api_port"]
    auto_accept: bool = False
    max_shard_bytes: int = 200 * 1024 * 1024
    quota_bytes: int = 2 * 1024 * 1024 * 1024
    max_chunk_chars: int = 20000
    llm_backend: str = "extractive"   # extractive | ollama | llamacpp
    llm_model: str = "qwen2.5:3b"
    refusal_cutoff: float | None = None   # None = use embedder default; set by `bitswarm calibrate`

    @classmethod
    def load(cls, path: Path) -> "Config":
        if path.exists():
            data = json.loads(path.read_text())
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        return cls()

    def save(self, path: Path):
        path.write_text(json.dumps(self.__dict__, indent=2))


class Home:
    """~/.bitswarm layout."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser()
        self.config_path = self.root / "config.json"
        self.identity_dir = self.root / "identity"
        self.trust_dir = self.root / "trust"
        self.seed_dir = self.root / "seed"
        self.incoming_dir = self.root / "incoming"
        self.torrent_dir = self.root / "torrents"
        self.store_dir = self.root / "query_store"
        self.logs_dir = self.root / "logs"
        self.audit_path = self.logs_dir / "audit.jsonl"

    def init(self) -> "Home":
        for d in (self.identity_dir, self.trust_dir, self.seed_dir, self.incoming_dir,
                  self.torrent_dir, self.store_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)
        os.chmod(self.identity_dir, 0o700)
        return self

    @property
    def config(self) -> Config:
        return Config.load(self.config_path)

    def audit(self, event: str, **fields):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        rec = {"ts": now(), "event": event, **fields}
        with open(self.audit_path, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def read_audit(self, limit: int = 200) -> list:
        if not self.audit_path.exists():
            return []
        lines = self.audit_path.read_text().splitlines()[-limit:]
        return [json.loads(x) for x in lines]

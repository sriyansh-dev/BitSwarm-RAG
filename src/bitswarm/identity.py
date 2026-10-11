"""ed25519 node identity, signing, trust list."""
from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from nacl.signing import SigningKey, VerifyKey
from nacl.exceptions import BadSignatureError
from .common import sha256_bytes, canonical


def fingerprint(pubkey_hex: str) -> str:
    return sha256_bytes(bytes.fromhex(pubkey_hex))[:16]


@dataclass
class Identity:
    sk: SigningKey
    name: str

    @property
    def pubkey(self) -> str:
        return self.sk.verify_key.encode().hex()

    @property
    def node_id(self) -> str:
        return fingerprint(self.pubkey)

    def sign(self, data: bytes) -> str:
        return self.sk.sign(data).signature.hex()

    def sign_obj(self, obj) -> str:
        return self.sign(canonical(obj))

    @classmethod
    def load_or_create(cls, identity_dir: Path, name: str = "node") -> "Identity":
        identity_dir.mkdir(parents=True, exist_ok=True)
        key_path = identity_dir / "node.key"
        name_path = identity_dir / "name.txt"
        if key_path.exists():
            sk = SigningKey(bytes.fromhex(key_path.read_text().strip()))
            name = name_path.read_text().strip() if name_path.exists() else name
        else:
            sk = SigningKey.generate()
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(sk.encode().hex())
            name_path.write_text(name)
            (identity_dir / "node.pub").write_text(sk.verify_key.encode().hex())
        return cls(sk, name)


def verify(pubkey_hex: str, data: bytes, sig_hex: str) -> bool:
    try:
        VerifyKey(bytes.fromhex(pubkey_hex)).verify(data, bytes.fromhex(sig_hex))
        return True
    except (BadSignatureError, ValueError, TypeError):
        return False


def verify_obj(pubkey_hex: str, obj, sig_hex: str) -> bool:
    return verify(pubkey_hex, canonical(obj), sig_hex)


class TrustList:
    """Directory of <name>.pub files, each holding a hex ed25519 public key."""

    def __init__(self, trust_dir: Path):
        self.dir = trust_dir

    def all(self) -> dict:
        out = {}
        if self.dir.exists():
            for p in sorted(self.dir.glob("*.pub")):
                out[p.read_text().strip()] = p.stem
        return out

    def is_trusted(self, pubkey_hex: str) -> bool:
        return pubkey_hex in self.all()

    def add(self, pubkey_hex: str, name: str):
        if len(bytes.fromhex(pubkey_hex)) != 32:
            raise ValueError("public key must be 64 hex characters (the LAST value printed by whoami)")
        safe = "".join(c for c in name if c.isalnum() or c in "-_") or "peer"
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"{safe}.pub").write_text(pubkey_hex)

    def remove(self, name: str):
        p = self.dir / f"{name}.pub"
        if p.exists():
            p.unlink()

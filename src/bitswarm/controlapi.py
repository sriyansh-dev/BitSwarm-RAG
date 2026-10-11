"""Control API (stdlib HTTP). Catalog + manifest + torrent + Range file serving (HTTP swarm transport)."""
from __future__ import annotations
import json, re, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .common import ALLOWED_SHARD_FILES, Home, sha256_bytes
from .shardpack import read_manifest

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def catalog_of(home: Home) -> list[dict]:
    out = []
    if home.seed_dir.exists():
        for d in sorted(home.seed_dir.iterdir()):
            if (d / "manifest.json").exists() and _HEX64.match(d.name):
                try:
                    m, _ = read_manifest(d)
                except Exception:
                    continue
                out.append({"shard_id": m["shard_id"], "title": m["title"],
                            "size": sum(f["size"] for f in m["files"]),
                            "publisher": m["publisher"], "expires_at": m.get("expires_at")})
    return out


def catalog_digest(home: Home) -> str:
    return sha256_bytes(json.dumps([c["shard_id"] for c in catalog_of(home)]).encode())[:16]


class _Bucket:
    def __init__(self, rate=50, burst=100):
        self.rate, self.burst, self.t = rate, burst, {}
        self.lock = threading.Lock()

    def allow(self, ip) -> bool:
        with self.lock:
            tokens, last = self.t.get(ip, (self.burst, time.time()))
            tokens = min(self.burst, tokens + (time.time() - last) * self.rate)
            if tokens < 1:
                self.t[ip] = (tokens, time.time()); return False
            self.t[ip] = (tokens - 1, time.time()); return True


def make_server(home: Home, identity, embedder_fp: dict, bind: str, port: int) -> ThreadingHTTPServer:
    bucket = _Bucket()

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, body: bytes, ctype="application/json", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not bucket.allow(self.client_address[0]):
                return self._send(429, b'{"error":"rate limited"}')
            p = self.path.split("?")[0]
            if p == "/v1/health":
                return self._send(200, json.dumps({"version": 1, "node_id": identity.node_id,
                                                   "name": identity.name, "embedding": embedder_fp}).encode())
            if p == "/v1/catalog":
                return self._send(200, json.dumps(catalog_of(home)).encode())
            m = re.match(r"^/v1/manifest/([0-9a-f]{64})$", p)
            if m:
                d = home.seed_dir / m.group(1)
                if not (d / "manifest.json").exists():
                    return self._send(404, b'{"error":"unknown shard"}')
                man, sig = read_manifest(d)
                return self._send(200, json.dumps({"manifest": man, "sig": sig}).encode())
            m = re.match(r"^/v1/torrent/([0-9a-f]{64})$", p)
            if m:
                t = home.torrent_dir / f"{m.group(1)}.torrent"
                if not t.exists():
                    return self._send(404, b'{"error":"no torrent"}')
                return self._send(200, t.read_bytes(), "application/x-bittorrent")
            m = re.match(r"^/v1/shard/([0-9a-f]{64})/(chunks\.parquet|vectors\.parquet|manifest\.json|manifest\.sig)$", p)
            if m:
                f = home.seed_dir / m.group(1) / m.group(2)
                if not f.is_file():
                    return self._send(404, b'{"error":"missing"}')
                size = f.stat().st_size
                rng = self.headers.get("Range")
                start, end = 0, size - 1
                if rng:
                    mm = re.match(r"bytes=(\d+)-(\d*)", rng)
                    if mm:
                        start = int(mm.group(1))
                        end = min(int(mm.group(2)) if mm.group(2) else size - 1, size - 1)
                    if start > end or start >= size:
                        return self._send(416, b"")
                with open(f, "rb") as fh:
                    fh.seek(start)
                    body = fh.read(end - start + 1)
                code = 206 if rng else 200
                return self._send(code, body, "application/octet-stream",
                                  {"Content-Range": f"bytes {start}-{end}/{size}", "Accept-Ranges": "bytes"})
            self._send(404, b'{"error":"not found"}')

    srv = ThreadingHTTPServer((bind, port), H)
    srv.daemon_threads = True
    return srv


def serve_background(srv: ThreadingHTTPServer) -> threading.Thread:
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return t

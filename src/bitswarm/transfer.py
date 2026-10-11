"""Transfer layer.
HttpSwarm   : always works. 256 KiB pieces fetched in parallel from ALL peers that have the shard (swarm effect),
              verified by manifest sha256 afterwards. Per-peer byte counters for the demo.
LibtorrentTransfer : real BitTorrent with DHT/PEX/LSD/trackers OFF, peers injected by our discovery."""
from __future__ import annotations
import json, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PIECE = 256 * 1024


def http_get(url: str, headers=None, timeout=10) -> bytes:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def http_json(url: str, timeout=10):
    return json.loads(http_get(url, timeout=timeout))


class HttpSwarm:
    def __init__(self, workers: int = 8):
        self.workers = workers

    def fetch(self, shard_id: str, manifest: dict, peers: list[str], dest: Path, progress=None) -> dict:
        """peers: ['ip:api_port', ...] that advertise this shard. Returns {peer: bytes}."""
        dest.mkdir(parents=True, exist_ok=True)
        per_peer = {p: 0 for p in peers}
        done = [0]
        total = sum(f["size"] for f in manifest["files"])
        lock = threading.Lock()
        dead: set = set()

        def get_piece(args):
            fname, off, ln, first = args
            order = peers[first % len(peers):] + peers[:first % len(peers)]   # round-robin start
            last = None
            for p in order:
                if p in dead:
                    continue
                try:
                    data = http_get(f"http://{p}/v1/shard/{shard_id}/{fname}",
                                    {"Range": f"bytes={off}-{off + ln - 1}"})
                    if len(data) != ln:
                        raise IOError("short read")
                    with lock:
                        per_peer[p] += len(data); done[0] += len(data)
                        if progress: progress(done[0], total, dict(per_peer))
                    return fname, off, data
                except Exception as e:
                    last = e
                    with lock:
                        dead.add(p) if "refused" in str(e).lower() else None
            raise IOError(f"all peers failed for {fname}@{off}: {last}")

        jobs, n = [], 0
        for f in manifest["files"]:
            for off in range(0, f["size"], PIECE):
                jobs.append((f["path"], off, min(PIECE, f["size"] - off), n)); n += 1
        bufs = {f["path"]: bytearray(f["size"]) for f in manifest["files"]}
        with ThreadPoolExecutor(self.workers) as ex:
            for fname, off, data in ex.map(get_piece, jobs):
                bufs[fname][off:off + len(data)] = data
        for fname, b in bufs.items():
            (dest / fname).write_bytes(bytes(b))
        # manifest + sig come from any peer (signature checked by the mount gate, not trusted here)
        for name in ("manifest.json", "manifest.sig"):
            for p in peers:
                try:
                    (dest / name).write_bytes(http_get(f"http://{p}/v1/shard/{shard_id}/{name}")); break
                except Exception:
                    continue
        return per_peer


class LibtorrentTransfer:
    """Private-team BitTorrent. Needs `pip install libtorrent`."""

    def __init__(self, port: int = 49502, bind: str = "0.0.0.0"):
        import libtorrent as lt
        self.lt = lt
        self.ses = lt.session({
            "listen_interfaces": f"{bind}:{port}",
            "enable_dht": False, "enable_lsd": False, "enable_upnp": False, "enable_natpmp": False,
            "anonymous_mode": False,
        })
        self.handles: dict = {}

    @staticmethod
    def create_torrent(shard_dir: Path, out_path: Path | None = None) -> tuple[bytes, str]:
        import libtorrent as lt
        fs = lt.file_storage()
        lt.add_files(fs, str(shard_dir))
        t = lt.create_torrent(fs, piece_size=PIECE)
        t.set_priv(True)
        lt.set_piece_hashes(t, str(shard_dir.parent))
        data = lt.bencode(t.generate())
        info = lt.torrent_info(lt.bdecode(data))
        if out_path:
            out_path.write_bytes(data)
        return data, str(info.info_hashes().v1)

    def add(self, torrent_bytes: bytes, save_path: Path, peers: list[tuple[str, int]], seed_mode=False):
        lt = self.lt
        info = lt.torrent_info(lt.bdecode(torrent_bytes))
        params = lt.add_torrent_params()
        params.ti = info
        params.save_path = str(save_path)
        if seed_mode:
            params.flags |= lt.torrent_flags.seed_mode
        h = self.ses.add_torrent(params)
        for ip, port in peers:
            h.connect_peer((ip, port))
        self.handles[str(info.info_hashes().v1)] = h
        return h

    def wait(self, h, timeout=60, progress=None) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            s = h.status()
            if progress: progress(s.progress, s.download_rate, s.num_peers)
            if s.is_seeding or s.progress >= 1.0:
                return True
            time.sleep(0.2)
        return False

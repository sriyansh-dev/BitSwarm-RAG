"""SPIKE 1: two libtorrent sessions in one process, different ports/dirs, DHT/PEX/LSD/trackers OFF,
peer added manually. Proves private-team BitTorrent works on this box."""
import sys, tempfile, time, pathlib, hashlib, os
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))
import libtorrent as lt
from bitswarm.transfer import LibtorrentTransfer


def main(size_mb=20):
    tmp = pathlib.Path(tempfile.mkdtemp())
    seed_root, leech_root = tmp / "seed", tmp / "leech"
    shard = seed_root / "demo_shard"
    shard.mkdir(parents=True); leech_root.mkdir()
    (shard / "chunks.parquet").write_bytes(os.urandom(size_mb * 1024 * 1024))
    (shard / "vectors.parquet").write_bytes(os.urandom(1024 * 1024))
    data, ih = LibtorrentTransfer.create_torrent(shard)
    print("libtorrent", lt.__version__, "| infohash", ih)
    seeder = LibtorrentTransfer(port=49802, bind="127.0.0.1")
    leecher = LibtorrentTransfer(port=49803, bind="127.0.0.1")
    seeder.add(data, seed_root, [], seed_mode=True)
    hl = leecher.add(data, leech_root, [("127.0.0.1", 49802)])
    t0 = time.time()
    ok = leecher.wait(hl, timeout=60)
    dt = time.time() - t0
    same = all(hashlib.sha256((shard / n).read_bytes()).digest() == hashlib.sha256((leech_root / "demo_shard" / n).read_bytes()).digest()
               for n in ("chunks.parquet", "vectors.parquet")) if ok else False
    print(f"download ok={ok} identical={same} time={dt:.2f}s ({(size_mb + 1) / max(dt, 1e-3):.1f} MB/s)")
    return ok and same


if __name__ == "__main__":
    sys.exit(0 if main() else 1)

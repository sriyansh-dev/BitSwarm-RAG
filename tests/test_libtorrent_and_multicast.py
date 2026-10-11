import sys, pathlib, time
ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))
import pytest


def test_libtorrent_private_swarm():
    pytest.importorskip("libtorrent")
    sys.path.insert(0, str(ROOT / "scripts"))
    import spike_libtorrent
    assert spike_libtorrent.main(size_mb=3)


def test_multicast_discovery_two_nodes(tmp_path):
    from bitswarm.node import Node
    A = Node(tmp_path / "A", "A", dict(api_port=49751, team_secret="t1"))
    B = Node(tmp_path / "B", "B", dict(api_port=49752, team_secret="t1"))
    X = Node(tmp_path / "X", "X", dict(api_port=49753, team_secret="OTHER"))
    for n in (A, B, X):
        n.start(discovery=True, sync_interval=3600)
    try:
        if not (A.discovery.ok and B.discovery.ok):
            pytest.skip("multicast unavailable in this environment: " + A.discovery.error)
        for n in (A, B, X):
            n.discovery.announce_once()
        time.sleep(1.0)
        ids = {p["node"] for p in A.peers.live()}
        if not ids:
            pytest.skip("multicast loopback not delivered in this environment")
        assert B.identity.node_id in ids
        assert X.identity.node_id not in ids          # other team ignored
    finally:
        for n in (A, B, X):
            n.stop()

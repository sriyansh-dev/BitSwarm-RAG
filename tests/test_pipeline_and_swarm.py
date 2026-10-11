import sys, pathlib, time, random
ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))
import pytest
from bitswarm.node import Node
from bitswarm.evaluate import evaluate, table
from bitswarm.discovery import build_announcement, parse_announcement
from bitswarm.identity import Identity
from bitswarm.common import now

CORPUS = ROOT / "eval" / "corpus"


def mk(tmp, name, port, **over):
    cfg = dict(api_port=port, bind="127.0.0.1", **over)
    return Node(tmp / name, name, cfg)


def test_local_rag_cites_real_chunks_and_refuses(tmp_path):
    a = mk(tmp_path, "a", 49701)
    a.publish(CORPUS, "OS notes")
    r = a.ask("What is the convoy effect in FCFS scheduling?")
    assert not r["refused"] and r["citations"]
    assert any("Scheduling" in c["doc_title"] for c in r["citations"])
    assert a.ask("How do I bake sourdough bread?")["refused"]


def test_hybrid_not_worse_than_single_modes(tmp_path):
    a = mk(tmp_path, "a", 49702)
    a.publish(CORPUS, "OS notes")
    res = evaluate(a, ROOT / "eval" / "questions.json")
    print("\n" + table(res), res["refusal"])
    assert res["hybrid"]["mrr"] >= min(res["dense"]["mrr"], res["bm25"]["mrr"]) - 1e-9
    assert res["hybrid"]["recall@20"] >= 0.9


def test_three_node_swarm_c_pulls_from_a_and_b(tmp_path):
    A, B, C = mk(tmp_path, "A", 49711), mk(tmp_path, "B", 49712), mk(tmp_path, "C", 49713)
    for n in (A, B, C):
        n.start(discovery=False, sync_interval=3600)
    try:
        for x in (B, C):
            x.trust.add(A.identity.pubkey, "A")
        # B and C know A and each other (manual peers)
        B.peers.add_manual(A.addr()); C.peers.add_manual(A.addr()); C.peers.add_manual(B.addr())
        shard = A.publish(CORPUS, "OS notes")
        sid = shard.name
        B.sync_once()
        assert sid in B.pending                      # offered, not auto accepted
        assert B.accept(sid)
        assert B.transfers[sid]["state"] == "mounted"
        assert B.ask("What is the convoy effect?")["citations"]
        C.sync_once()
        assert C.accept(sid)
        pp = C.transfers[sid]["per_peer"]
        assert pp[A.addr()] > 0 and pp[B.addr()] > 0, pp   # swarm effect: both seeders served C
        assert C.ask("Explain thrashing")["citations"]
        steps = C.transfers[sid]["steps"]
        assert [s["step"] for s in steps] == [1, 2, 3, 4, 5, 6, 7, 8] and all(s["ok"] for s in steps)
    finally:
        for n in (A, B, C):
            n.stop()


def test_untrusted_publisher_never_downloads(tmp_path):
    A, B = mk(tmp_path, "A", 49721), mk(tmp_path, "B", 49722)
    A.start(discovery=False, sync_interval=3600)
    try:
        B.peers.add_manual(A.addr())
        A.publish(CORPUS, "OS notes")
        B.sync_once()
        assert not B.pending and len(B.rejected) == 1
        assert "not in trust list" in list(B.rejected.values())[0]
        assert not list(B.home.incoming_dir.glob("*"))
    finally:
        A.stop()


def test_discovery_message_security():
    ident = Identity.load_or_create(pathlib.Path("/tmp/bs_disc_id"), "x")
    good = build_announcement(ident, "secret", 1, 2, "d")
    assert parse_announcement(good, "secret")
    assert parse_announcement(good, "other-team") is None                    # wrong team
    assert parse_announcement(build_announcement(ident, "secret", 1, 2, "d", ts=now() - 120), "secret") is None  # replay
    assert parse_announcement(good.replace(b'"api_port":1', b'"api_port":9'), "secret") is None  # tamper
    assert parse_announcement(b"garbage", "secret") is None


def test_expiry_auto_unmounts(tmp_path):
    a = mk(tmp_path, "a", 49731)
    d = a.publish(CORPUS, "temp", expires_in=1)
    assert a.store.total_chunks() > 0
    time.sleep(2.1)
    a.expire()
    assert a.store.total_chunks() == 0 and not d.exists()

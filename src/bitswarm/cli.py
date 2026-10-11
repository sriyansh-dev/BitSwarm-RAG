"""bitswarm CLI."""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path
from .common import Config, Home
from .node import Node


def _node(a) -> Node:
    over = {}
    for k in ("embedder", "llm_backend", "llm_model", "api_port", "bind", "team_secret"):
        v = getattr(a, k, None)
        if v is not None:
            over[k] = v
    n = Node(a.home, getattr(a, "name", None) or "node", over)
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bitswarm")
    ap.add_argument("--home", default="~/.bitswarm")
    ap.add_argument("--name", default=None)
    ap.add_argument("--embedder", choices=["hash", "bge"])
    ap.add_argument("--llm-backend", dest="llm_backend", choices=["extractive", "ollama", "llamacpp"])
    ap.add_argument("--llm-model", dest="llm_model")
    ap.add_argument("--api-port", dest="api_port", type=int)
    ap.add_argument("--bind")
    ap.add_argument("--team-secret", dest="team_secret")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    p = sub.add_parser("pack"); p.add_argument("src"); p.add_argument("--title", required=True)
    p.add_argument("--expires-in", type=int, help="seconds"); p.add_argument("--redact-paths", action="store_true")
    p.add_argument("--chunk-target", type=int, default=400, help="words per chunk")
    p = sub.add_parser("ask"); p.add_argument("question"); p.add_argument("--mode", default="hybrid")
    p = sub.add_parser("trust"); p.add_argument("pubkey"); p.add_argument("peer_name")
    sub.add_parser("whoami")
    p = sub.add_parser("serve"); p.add_argument("--ui-port", type=int, default=8080); p.add_argument("--peer", action="append", default=[])
    p.add_argument("--auto-accept", action="store_true"); p.add_argument("--no-discovery", action="store_true")
    p = sub.add_parser("eval"); p.add_argument("questions"); p.add_argument("--corpus"); p.add_argument("--chunk-target", type=int, default=400)
    p = sub.add_parser("calibrate"); p.add_argument("questions"); p.add_argument("--corpus"); p.add_argument("--chunk-target", type=int, default=400)
    p.add_argument("--save", action="store_true", help="write suggested cutoff to config")
    sub.add_parser("attack")
    a = ap.parse_args(argv)

    if a.cmd == "attack":
        from .attack import run_all
        res = run_all()
        for r in res:
            print(("PASS" if r.passed else "FAIL"), r.name.ljust(28), r.expected.ljust(16), r.got.ljust(16), r.detail[:60])
        print(f"{sum(r.passed for r in res)}/{len(res)} behaved correctly")
        return 0 if all(r.passed for r in res) else 1

    n = _node(a)
    if a.cmd == "init":
        print("home:", n.home.root, "\nnode:", n.identity.node_id, "\npubkey:", n.identity.pubkey)
    elif a.cmd == "whoami":
        print(n.identity.name, n.identity.node_id, n.identity.pubkey)
    elif a.cmd == "trust":
        try:
            n.trust.add(a.pubkey, a.peer_name)
        except ValueError as e:
            print("error:", e); return 1
        print("trusted", a.peer_name)
    elif a.cmd == "pack":
        d = n.publish(a.src, a.title, expires_in=a.expires_in, redact_paths=a.redact_paths, chunk_target=a.chunk_target)
        print("shard:", d.name, "\npath:", d)
    elif a.cmd == "ask":
        r = n.ask(a.question, a.mode)
        print(r["answer"])
        for c in r["citations"]:
            print(f"  [{c['tag']}] {c['doc_title']} > {c['section']}  (shard '{c['shard_title']}', by {c['publisher']})")
        for w in r["warnings"]:
            print("  !", w)
    elif a.cmd == "eval":
        from .evaluate import evaluate, table
        if a.corpus:
            n.publish(a.corpus, "eval-corpus", chunk_target=a.chunk_target)
        res = evaluate(n, Path(a.questions))
        print(table(res)); print("\nrefusal:", json.dumps(res["refusal"]))
        Path("eval/results").mkdir(parents=True, exist_ok=True)
        Path("eval/results/latest.json").write_text(json.dumps(res, indent=2))
    elif a.cmd == "calibrate":
        from .evaluate import calibrate
        if a.corpus:
            n.publish(a.corpus, "eval-corpus", chunk_target=a.chunk_target)
        c = calibrate(n, Path(a.questions))
        print(json.dumps(c, indent=2))
        if a.save:
            n.cfg.refusal_cutoff = c["suggested_cutoff"]; n.cfg.save(n.home.config_path)
            print("saved refusal_cutoff =", c["suggested_cutoff"], "to", n.home.config_path)
    elif a.cmd == "serve":
        import uvicorn
        from .ui import create_app
        n.cfg.auto_accept = a.auto_accept
        for peer in a.peer:
            n.peers.add_manual(peer)
        n.start(discovery=not a.no_discovery)
        print(f"node {n.identity.name} api={n.addr()} ui=http://127.0.0.1:{a.ui_port}")
        uvicorn.run(create_app(n), host="127.0.0.1", port=a.ui_port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())

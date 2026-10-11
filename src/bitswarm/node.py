"""Node = everything wired together: identity, store, mount, API, discovery, sync."""
from __future__ import annotations
import collections, shutil, threading, time
from pathlib import Path
from .common import Config, Home, now
from .controlapi import catalog_digest, catalog_of, make_server, serve_background
from .discovery import Discovery, PeerTable
from .embed import fingerprint as embed_fp, get_embedder
from .identity import Identity, TrustList
from .indexer import build_chunks, collect_files
from .mount import MountError, check_manifest_light, mount
from .retrieval import Retriever
from .shardpack import pack, read_manifest
from .store import Store
from .synth import answer as synth_answer, get_backend
from .transfer import HttpSwarm, http_json


class Node:
    def __init__(self, root: str | Path, name: str = "node", cfg_over: dict | None = None):
        self.home = Home(root).init()
        self.cfg = self.home.config
        for k, v in (cfg_over or {}).items():
            setattr(self.cfg, k, v)
        if not self.home.config_path.exists():
            self.cfg.save(self.home.config_path)
        self.identity = Identity.load_or_create(self.home.identity_dir, name)
        self.embedder = get_embedder(self.cfg.embedder)
        self.store = Store(self.home.store_dir, self.embedder.dim)
        self.trust = TrustList(self.home.trust_dir)
        self.trust.add(self.identity.pubkey, "self")
        self.retriever = Retriever(self.store, self.embedder)
        self.backend = get_backend(self.cfg)
        self.peers = PeerTable()
        for p in self.cfg.manual_peers:
            self.peers.add_manual(p)
        self.pending: dict = {}      # shard_id -> {manifest, peers}
        self.rejected: dict = {}     # shard_id -> reason
        self.transfers: dict = {}    # shard_id -> progress dict
        self.events = collections.deque(maxlen=200)
        self._servers, self._stop = [], threading.Event()
        self.discovery = None
        self._remount_seed()

    # ---- helpers ----------------------------------------------------------
    def log(self, kind: str, **kw):
        self.events.append({"ts": now(), "kind": kind, **kw})

    def _remount_seed(self):
        """Query store is disposable: rebuild from seed store if shards are missing."""
        have = self.store.mounted_ids()
        for d in self.home.seed_dir.glob("*"):
            if d.is_dir() and d.name not in have:
                try:
                    mount(self.home, d, self.embedder, self.store, self.trust, self.cfg)
                except MountError as e:
                    self.rejected[d.name] = e.reason

    def addr(self) -> str:
        return f"127.0.0.1:{self.cfg.api_port}"

    # ---- publish / ask -----------------------------------------------------
    def publish(self, src: str | Path, title: str, description="", tags=None, expires_in: int | None = None,
                redact_paths=False, chunk_target: int = 400) -> Path:
        src = Path(src)
        files = collect_files(src)
        if not files:
            raise ValueError("no .md/.txt/.pdf files found")
        recs, vecs = build_chunks(files, self.embedder, target=chunk_target, redact_paths=redact_paths,
                                  root=src if src.is_dir() else src.parent)
        d = pack(self.home.seed_dir, self.identity, recs, vecs, self.embedder, title=title, description=description,
                 tags=tags, expires_at=(now() + expires_in) if expires_in else None, redact_paths=redact_paths,
                 chunk_target=chunk_target)
        mount(self.home, d, self.embedder, self.store, self.trust, self.cfg)
        try:
            from .transfer import LibtorrentTransfer
            LibtorrentTransfer.create_torrent(d, self.home.torrent_dir / f"{d.name}.torrent")
        except Exception:
            pass
        self.log("published", shard_id=d.name, title=title)
        return d

    def ask(self, question: str, mode="hybrid") -> dict:
        cutoff = self.cfg.refusal_cutoff if self.cfg.refusal_cutoff is not None else self.embedder.refusal_cutoff
        return synth_answer(question, self.retriever, self.backend, cutoff, mode=mode)

    def expire(self):
        for sid in self.store.expired(now()):
            self.store.unmount(sid)
            shutil.rmtree(self.home.seed_dir / sid, ignore_errors=True)
            self.home.audit("expired_unmounted", shard_id=sid)
            self.log("expired", shard_id=sid)

    # ---- sync --------------------------------------------------------------
    def sync_once(self) -> list[dict]:
        ev_start = len(self.events)
        catalogs: dict[str, list] = {}
        for p in self.peers.live():
            addr = f"{p['ip']}:{p['api_port']}"
            if addr == self.addr() and p.get("manual"):
                continue
            try:
                catalogs[addr] = http_json(f"http://{addr}/v1/catalog", timeout=3)
            except Exception as e:
                self.log("peer_unreachable", peer=addr, error=str(e)[:80])
        known = self.store.mounted_ids() | {d.name for d in self.home.seed_dir.glob("*")}
        by_shard: dict[str, list[str]] = {}
        for addr, cat in catalogs.items():
            for e in cat:
                by_shard.setdefault(e["shard_id"], []).append(addr)
        for sid, addrs in by_shard.items():
            if sid in known or sid in self.rejected or sid in self.transfers:
                continue
            if sid in self.pending:
                self.pending[sid]["peers"] = addrs
                continue
            try:
                r = http_json(f"http://{addrs[0]}/v1/manifest/{sid}")
                man, sig = r["manifest"], r["sig"]
                if man.get("shard_id") != sid:
                    raise MountError(1, "advertised shard_id != manifest shard_id")
                check_manifest_light(man, sig, self.trust, self.embedder, self.cfg)
            except MountError as e:
                self.rejected[sid] = f"step {e.step}: {e.reason}"
                self.home.audit("precheck_rejected", shard_id=sid, step=e.step, reason=e.reason)
                self.log("rejected", shard_id=sid, reason=self.rejected[sid])
                continue
            except Exception as e:
                self.log("manifest_fetch_failed", shard_id=sid, error=str(e)[:80])
                continue
            self.pending[sid] = {"manifest": man, "peers": addrs}
            self.log("shard_offered", shard_id=sid, title=man["title"], publisher=man["publisher"]["name"])
            if self.cfg.auto_accept:
                self.accept(sid)
        return list(self.events)[ev_start:]

    def accept(self, sid: str) -> bool:
        item = self.pending.pop(sid, None)
        if not item:
            return False
        man, peers = item["manifest"], item["peers"]
        dest = self.home.incoming_dir / sid
        shutil.rmtree(dest, ignore_errors=True)
        prog = {"state": "downloading", "done": 0, "total": 1, "per_peer": {}, "steps": []}
        self.transfers[sid] = prog

        def on_dl(done, total, per_peer):
            prog.update(done=done, total=total, per_peer=per_peer)
        try:
            t0 = time.time()
            per_peer = HttpSwarm().fetch(sid, man, peers, dest, on_dl)
            prog.update(state="verifying", dl_seconds=round(time.time() - t0, 3), per_peer=per_peer)
            t1 = time.time()
            res = mount(self.home, dest, self.embedder, self.store, self.trust, self.cfg,
                        progress=lambda s: prog["steps"].append(s))
            prog.update(state="mounted", mount_seconds=round(time.time() - t1, 3))
            self.log("mounted", shard_id=sid, from_peers=per_peer)
            return True
        except MountError as e:
            prog.update(state="rejected", reason=f"step {e.step}: {e.reason}")
            self.rejected[sid] = prog["reason"]
            self.log("rejected", shard_id=sid, reason=prog["reason"])
            return False
        except Exception as e:
            prog.update(state="failed", reason=str(e)[:200])
            self.log("transfer_failed", shard_id=sid, error=str(e)[:120])
            self.transfers.pop(sid, None)
            return False
        finally:
            shutil.rmtree(dest, ignore_errors=True)

    def reject(self, sid: str):
        self.pending.pop(sid, None)
        self.rejected[sid] = "rejected by user"

    # ---- services ------------------------------------------------------------
    def start(self, discovery=True, sync_interval=3.0):
        srv = make_server(self.home, self.identity, embed_fp(self.embedder), self.cfg.bind, self.cfg.api_port)
        serve_background(srv)
        self._servers.append(srv)
        if discovery:
            self.discovery = Discovery(self.identity, self.cfg, self.peers, lambda: catalog_digest(self.home))
            self.discovery.start()
            if not self.discovery.ok:
                self.log("discovery_off", error=self.discovery.error)

        def loop():
            while not self._stop.is_set():
                try:
                    self.sync_once(); self.expire()
                except Exception as e:
                    self.log("sync_error", error=str(e)[:100])
                self._stop.wait(sync_interval)
        threading.Thread(target=loop, daemon=True).start()
        return self

    def stop(self):
        self._stop.set()
        if self.discovery:
            self.discovery.stop()
        for s in self._servers:
            s.shutdown(); s.server_close()

    def swarm_view(self) -> dict:
        me = {"id": self.identity.node_id, "name": self.identity.name, "self": True}
        nodes, edges = [me], []
        for p in self.peers.live():
            nodes.append({"id": p["node"], "name": p.get("name", p["node"]), "ip": p["ip"], "manual": p["manual"]})
            edges.append({"a": me["id"], "b": p["node"]})
        return {"nodes": nodes, "edges": edges, "transfers": self.transfers,
                "shards": self.store.shards(), "pending": {k: {"title": v["manifest"]["title"],
                "publisher": v["manifest"]["publisher"]["name"], "peers": v["peers"]} for k, v in self.pending.items()},
                "rejected": self.rejected, "discovery": {"ok": bool(self.discovery and self.discovery.ok),
                "error": self.discovery.error if self.discovery else "off"}}

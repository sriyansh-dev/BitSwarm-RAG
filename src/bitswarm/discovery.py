"""UDP multicast discovery. Announcements are ed25519-signed (node identity) AND HMAC'd with the team secret
(team membership). Replay window +-30s. Peers expire after ~3 missed intervals. Manual peers always supported."""
from __future__ import annotations
import hashlib, hmac, json, random, socket, struct, threading, time
from .common import canonical, now
from .identity import Identity, fingerprint, verify

INTERVAL = 5.0
WINDOW = 30
PEER_TTL = INTERVAL * 3 + 2


def team_hash(secret: str) -> str:
    return hashlib.sha256(("bitswarm-team:" + secret).encode()).hexdigest()[:16]


def _mac(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def build_announcement(identity: Identity, secret: str, api_port: int, bt_port: int, digest: str, ts: int | None = None) -> bytes:
    body = {"v": 1, "team": team_hash(secret), "node": identity.node_id, "pk": identity.pubkey,
            "name": identity.name, "api_port": api_port, "bt_port": bt_port, "digest": digest, "ts": ts or now()}
    cb = canonical(body)
    return canonical({"body": body, "sig": identity.sign(cb), "mac": _mac(secret, cb)})


def parse_announcement(raw: bytes, secret: str, now_ts: int | None = None) -> dict | None:
    """Return body if valid else None. Never raises."""
    try:
        msg = json.loads(raw)
        body, sig, mac = msg["body"], msg["sig"], msg["mac"]
        cb = canonical(body)
        if body.get("v") != 1 or body["team"] != team_hash(secret):
            return None
        if not hmac.compare_digest(mac, _mac(secret, cb)):
            return None
        if fingerprint(body["pk"]) != body["node"] or not verify(body["pk"], cb, sig):
            return None
        if abs((now_ts or now()) - int(body["ts"])) > WINDOW:
            return None
        return body
    except Exception:
        return None


class PeerTable:
    def __init__(self):
        self.lock = threading.Lock()
        self.peers: dict[str, dict] = {}     # node_id -> info

    def update(self, body: dict, ip: str, manual=False):
        with self.lock:
            self.peers[body["node"]] = {**body, "ip": ip, "last_seen": time.time(), "manual": manual}

    def add_manual(self, addr: str):
        ip, port = addr.rsplit(":", 1)
        with self.lock:
            self.peers["manual:" + addr] = {"node": "manual:" + addr, "name": addr, "ip": ip, "api_port": int(port),
                                            "bt_port": 0, "digest": "", "last_seen": time.time(), "manual": True}

    def live(self) -> list[dict]:
        t = time.time()
        with self.lock:
            for k in [k for k, v in self.peers.items() if not v["manual"] and t - v["last_seen"] > PEER_TTL]:
                del self.peers[k]
            for v in self.peers.values():
                if v["manual"]:
                    v["last_seen"] = t     # manual peers never expire; sync marks failures
            return list(self.peers.values())


class Discovery:
    def __init__(self, identity: Identity, cfg, table: PeerTable, digest_fn):
        self.id, self.cfg, self.table, self.digest_fn = identity, cfg, table, digest_fn
        self._stop = threading.Event()
        self.threads: list[threading.Thread] = []
        self.ok = True
        self.error = ""

    def _sock(self) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try: s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError: pass
        return s

    def start(self):
        try:
            self.rx = self._sock()
            self.rx.bind(("", self.cfg.mcast_port))
            mreq = struct.pack("4sl", socket.inet_aton(self.cfg.mcast_group), socket.INADDR_ANY)
            self.rx.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            self.rx.settimeout(1.0)
            self.tx = self._sock()
            self.tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)      # LAN only
            self.tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        except OSError as e:
            self.ok, self.error = False, f"multicast unavailable: {e} (use manual peers)"
            return
        for fn in (self._listen, self._announce):
            t = threading.Thread(target=fn, daemon=True); t.start(); self.threads.append(t)

    def announce_once(self):
        msg = build_announcement(self.id, self.cfg.team_secret, self.cfg.api_port, self.cfg.bt_port, self.digest_fn())
        self.tx.sendto(msg, (self.cfg.mcast_group, self.cfg.mcast_port))

    def _announce(self):
        while not self._stop.is_set():
            try: self.announce_once()
            except OSError: pass
            self._stop.wait(INTERVAL + random.uniform(-1, 1))

    def _listen(self):
        while not self._stop.is_set():
            try:
                raw, (ip, _) = self.rx.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            body = parse_announcement(raw, self.cfg.team_secret)
            if body and body["node"] != self.id.node_id:
                self.table.update(body, ip)

    def stop(self):
        self._stop.set()

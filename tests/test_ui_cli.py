import sys, pathlib
ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))
from fastapi.testclient import TestClient
from bitswarm.node import Node
from bitswarm.ui import create_app
from bitswarm.cli import main


def test_ui_api(tmp_path):
    n = Node(tmp_path / "n", "n", dict(api_port=49741))
    n.publish(ROOT / "eval" / "corpus", "OS")
    c = TestClient(create_app(n))
    assert c.get("/").status_code == 200
    r = c.post("/api/ask", json={"q": "What is thrashing?"}).json()
    assert not r["refused"] and r["provenance"]["chunks"] >= 1
    assert c.get("/api/swarm").json()["shards"]
    assert c.get("/api/whoami").json()["pubkey"]
    atk = c.post("/api/attack").json()
    assert all(a["passed"] for a in atk)
    assert c.post("/api/trust", json={"pubkey": "zz", "name": "x"}).status_code == 400


def test_cli_roundtrip(tmp_path, capsys):
    h = str(tmp_path / "home")
    assert main(["--home", h, "pack", str(ROOT / "eval" / "corpus"), "--title", "OS"]) == 0
    assert main(["--home", h, "ask", "What are the four Coffman conditions?"]) == 0
    out = capsys.readouterr().out
    assert "mutual exclusion" in out.lower() and "[C1]" in out


def test_trust_cli_rejects_short_id_and_keeps_name(tmp_path, capsys):
    h = str(tmp_path / "h")
    assert main(["--home", h, "--name", "bob", "trust", "0123456789abcdef", "alice"]) == 1     # 16-char id, not a key
    key = "ab" * 32
    assert main(["--home", h, "--name", "bob", "trust", key, "alice"]) == 0
    assert (pathlib.Path(h) / "identity" / "name.txt").read_text().strip() == "bob"             # --name not clobbered

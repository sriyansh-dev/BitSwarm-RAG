import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))
from bitswarm.attack import run_all


def test_all_attacks_rejected_at_correct_gate():
    results = run_all()
    bad = [r for r in results if not r.passed]
    assert not bad, "\n".join(f"{r.name}: expected {r.expected}, got {r.got} ({r.detail})" for r in bad)
    assert len(results) >= 15

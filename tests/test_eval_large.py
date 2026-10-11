import sys, pathlib, json, re
ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))
from bitswarm.node import Node
from bitswarm.evaluate import evaluate, table, calibrate


def test_every_expected_doc_exists_in_corpus():
    titles = set()
    for p in (ROOT / "eval" / "corpus_large").glob("*.md"):
        m = re.search(r"^# (.+)$", p.read_text(), re.M)
        titles.add(m.group(1).strip())
    qs = json.loads((ROOT / "eval" / "questions_large.json").read_text())
    missing = {d for q in qs["answerable"] for d in q["expected_docs"]} - titles
    assert not missing, missing
    assert len(titles) >= 20 and len(qs["answerable"]) >= 60


def test_large_eval_and_calibrate_run(tmp_path):
    n = Node(tmp_path / "n", "n", dict(api_port=49761))
    n.publish(ROOT / "eval" / "corpus_large", "big", chunk_target=80)
    assert n.store.total_chunks() > 40
    res = evaluate(n, ROOT / "eval" / "questions_large.json")
    print("\n" + table(res))
    assert set(res["hybrid"]["by_type"]) == {"lexical", "paraphrase", "mixed"}
    c = calibrate(n, ROOT / "eval" / "questions_large.json")
    assert 0 < c["balanced_accuracy"] <= 1

"""Retrieval eval. Metrics: Hit@1, Recall@5, Recall@20, MRR, nDCG@10 for dense vs bm25 vs hybrid,
broken down by question type (lexical / paraphrase / mixed), plus refusal correctness and cutoff calibration."""
from __future__ import annotations
import json, math
from pathlib import Path

MODES = ("dense", "bm25", "hybrid")
METRICS = ("hit@1", "recall@5", "recall@20", "mrr", "ndcg@10")


def _score(titles: list[str], exp: set) -> dict:
    rel = [1 if t in exp else 0 for t in titles]
    dcg = sum(x / math.log2(i + 2) for i, x in enumerate(rel[:10]))
    ideal = sum(1 / math.log2(i + 2) for i in range(min(10, max(sum(rel), 1))))
    return {"hit@1": float(any(rel[:1])), "recall@5": float(any(rel[:5])), "recall@20": float(any(rel[:20])),
            "mrr": next((1.0 / (i + 1) for i, x in enumerate(rel) if x), 0.0),
            "ndcg@10": dcg / ideal if ideal else 0.0}


def evaluate(node, questions_path: Path, modes=MODES) -> dict:
    qs = json.loads(Path(questions_path).read_text())
    out: dict = {}
    for mode in modes:
        rows = []
        for item in qs["answerable"]:
            hits = node.retriever.search(item["q"], mode=mode, top=20)["hits"]
            rows.append((item.get("type", "all"), _score([h["doc_title"] for h in hits], set(item["expected_docs"]))))
        n = len(rows)
        res = {m: sum(r[m] for _, r in rows) / n for m in METRICS}
        res["by_type"] = {}
        for t in sorted({t for t, _ in rows}):
            sub = [r for tt, r in rows if tt == t]
            res["by_type"][t] = {"n": len(sub), **{m: sum(r[m] for r in sub) / len(sub) for m in ("hit@1", "mrr")}}
        out[mode] = res
    unans = qs.get("unanswerable", [])
    refused = sum(1 for q in unans if node.ask(q)["refused"])
    answered = sum(1 for it in qs["answerable"] if not node.ask(it["q"])["refused"])
    out["refusal"] = {"unanswerable_refused": f"{refused}/{len(unans)}",
                      "answerable_answered": f"{answered}/{len(qs['answerable'])}"}
    out["n_questions"] = len(qs["answerable"])
    return out


def table(res: dict) -> str:
    L = [f"Overall ({res.get('n_questions', '?')} answerable questions)", "",
         "| mode | Hit@1 | Recall@5 | Recall@20 | MRR | nDCG@10 |", "|---|---|---|---|---|---|"]
    for m in MODES:
        if m in res:
            r = res[m]
            L.append(f"| {m} | {r['hit@1']:.3f} | {r['recall@5']:.3f} | {r['recall@20']:.3f} | {r['mrr']:.3f} | {r['ndcg@10']:.3f} |")
    types = sorted(res["hybrid"]["by_type"]) if "hybrid" in res else []
    if types:
        L += ["", "MRR by question type", "", "| mode | " + " | ".join(f"{t} (n={res['hybrid']['by_type'][t]['n']})" for t in types) + " |",
              "|---|" + "---|" * len(types)]
        for m in MODES:
            if m in res:
                L.append(f"| {m} | " + " | ".join(f"{res[m]['by_type'][t]['mrr']:.3f}" for t in types) + " |")
    if all(m in res for m in MODES):
        h, d, b = res["hybrid"], res["dense"], res["bm25"]
        wins = [k for k in ("hit@1", "mrr", "ndcg@10") if h[k] > max(d[k], b[k]) + 1e-9]
        ties = [k for k in ("hit@1", "mrr", "ndcg@10") if abs(h[k] - max(d[k], b[k])) <= 1e-9]
        L += ["", f"Goal check (hybrid beats BOTH single methods): wins on {wins or 'none'}; ties best single on {ties or 'none'}."]
    return "\n".join(L)


def calibrate(node, questions_path: Path) -> dict:
    """Pick refusal cutoff from best-dense-cosine of answerable vs unanswerable questions."""
    qs = json.loads(Path(questions_path).read_text())
    best = lambda q: node.retriever.search(q, mode="dense", top=1)["best_dense"] or 0.0
    a = sorted(best(it["q"]) for it in qs["answerable"])
    u = sorted(best(q) for q in qs["unanswerable"])
    # choose threshold maximising balanced accuracy over all candidate midpoints
    cands = sorted(set(a + u))
    mids = [(x + y) / 2 for x, y in zip(cands, cands[1:])] or [0.0]
    def bal(t):
        return 0.5 * (sum(x >= t for x in a) / len(a) + sum(x < t for x in u) / len(u))
    t = max(mids, key=bal)
    return {"answerable_min": a[0], "answerable_p10": a[max(0, int(0.1 * len(a)))], "unanswerable_max": u[-1],
            "separable": a[0] > u[-1], "suggested_cutoff": round(t, 4), "balanced_accuracy": round(bal(t), 3)}

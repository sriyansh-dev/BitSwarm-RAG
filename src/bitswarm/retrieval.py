"""Hybrid retrieval: dense + BM25 -> RRF -> optional rerank. Modes exist so eval can compare them."""
from __future__ import annotations
from .store import Store, rrf


class Retriever:
    def __init__(self, store: Store, embedder, reranker=None):
        self.store, self.embedder, self.reranker = store, embedder, reranker

    def search(self, query: str, mode: str = "hybrid", top: int = 5, k_each: int = 50, fuse_n: int = 20) -> dict:
        qv = self.embedder.embed_query(query)
        dense = self.store.dense(qv, k_each) if mode in ("dense", "hybrid") else []
        bm = self.store.bm25(query, k_each) if mode in ("bm25", "hybrid") else []
        dense_score = {rid: s for rid, s in dense}
        if mode == "dense":
            order = [rid for rid, _ in dense]
        elif mode == "bm25":
            order = [rid for rid, _ in bm]
        else:
            order = [rid for rid, _ in rrf([[r for r, _ in dense], [r for r, _ in bm]])][:fuse_n]
        hits, seen = [], set()
        for rid in order:
            h = self.store.get(rid)
            if not h or h["chunk_id"] in seen:       # dedupe identical chunks across shards
                continue
            seen.add(h["chunk_id"])
            h["dense_score"] = dense_score.get(rid)
            hits.append(h)
        if self.reranker and hits:
            scores = self.reranker.score(query, [h["text"] for h in hits])
            hits = [h for _, h in sorted(zip(scores, hits), key=lambda x: -x[0])]
        best_dense = max(dense_score.values()) if dense_score else None
        if best_dense is None and mode != "dense":     # need a cosine for refusal even in bm25 mode
            best_dense = max((s for _, s in self.store.dense(qv, 5)), default=None)
        return {"hits": hits[:top], "best_dense": best_dense}

"""Parse -> chunk -> embed. Produces chunk records + vectors ready for shardpack."""
from __future__ import annotations
import re
from pathlib import Path
import numpy as np
from .common import sha256_bytes

SUPPORTED = {".md", ".txt", ".pdf"}
_WORD = re.compile(r"\S+")
_HEAD = re.compile(r"^(#{1,6})\s+(.*)$", re.M)


def parse_file(path: Path) -> tuple[str, str]:
    """Return (title, text)."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        r = PdfReader(str(path))
        text = "\n\n".join((pg.extract_text() or "") for pg in r.pages)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    m = _HEAD.search(text)
    title = m.group(2).strip() if (m and m.group(1) == "#") else path.stem.replace("_", " ")
    return title, text


def embed_text(doc_title: str, section: str, text: str) -> str:
    """The exact string that gets embedded. Mount gate re-derives it to re-embed samples."""
    return f"{doc_title} > {section}\n{text}"


def chunk_text(text: str, target: int = 400, overlap: float = 0.12):
    """Word-window chunks (~target words), snapped to paragraph breaks, with overlap.
    Yields (start_char, end_char, section_heading)."""
    toks = [(m.start(), m.end()) for m in _WORD.finditer(text)]
    heads = [(m.start(), m.group(2).strip()) for m in _HEAD.finditer(text)]
    n = len(toks)
    ov = max(1, int(target * overlap))
    i = 0
    while i < n:
        j = min(i + target, n)
        if j < n:
            lo = i + int(target * 0.7)
            for k in range(j, lo, -1):          # latest paragraph break in last 30%
                if "\n\n" in text[toks[k - 1][1]:toks[k][0]]:
                    j = k
                    break
        s, e = toks[i][0], toks[j - 1][1]
        sec = ""
        for pos, h in heads:
            if pos <= s:
                sec = h
            else:
                break
        yield s, e, sec
        if j >= n:
            break
        i = max(j - ov, i + 1)


def build_chunks(paths: list[Path], embedder, target: int = 400, overlap: float = 0.12,
                 redact_paths: bool = False, root: Path | None = None):
    recs, texts = [], []
    for p in paths:
        title, text = parse_file(p)
        doc_hash = sha256_bytes(text.encode("utf-8"))
        doc_id = doc_hash[:16]
        ref = "redacted" if redact_paths else (str(p.relative_to(root)) if root else p.name)
        for idx, (s, e, sec) in enumerate(chunk_text(text, target, overlap)):
            body = text[s:e]
            cid = sha256_bytes(f"{doc_hash}|{idx}|{body}".encode("utf-8"))
            recs.append({"chunk_id": cid, "doc_id": doc_id, "doc_title": title, "source_ref": ref,
                         "chunk_index": idx, "char_start": s, "char_end": e,
                         "section": sec, "text": body})
            texts.append(embed_text(title, sec, body))
    vecs = np.zeros((0, embedder.dim), np.float32)
    for i in range(0, len(texts), 64):
        batch = embedder.embed(texts[i:i + 64])
        vecs = np.vstack([vecs, batch])
    return recs, vecs


def collect_files(src: Path) -> list[Path]:
    if src.is_file():
        return [src]
    return sorted(p for p in src.rglob("*") if p.suffix.lower() in SUPPORTED and p.is_file())

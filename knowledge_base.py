"""Local knowledge base for the meeting workflow.

Storage strategy: chunks + metadata persisted as JSONL at ~/.meeting_workflow/kb.jsonl.
Retrieval: TF-IDF + cosine similarity via scikit-learn. The TF-IDF matrix is rebuilt
in-memory on the first retrieval after any change.

Why not ChromaDB / sentence-transformers? The PyTorch/ONNX-based embedders segfault
on this user's Anaconda environment (PyTorch + Tk + OpenMP conflict). TF-IDF gives
us keyword-overlap retrieval that's reliable, has no native-code crash risk, and is
fast for thousands of chunks. Quality is worse than semantic embeddings on synonym
queries, but good enough to surface relevant chunks for an LLM to use.

Public API:
  - add_document(path)              -> dict
  - index_transcript(text, title)   -> dict
  - retrieve(query, k=6, kind=None) -> list[Chunk]
  - list_documents()                -> list[dict]
  - delete_document(doc_id)         -> int
  - reset()                         -> None
  - warm_up()                       -> None  (loads the index from disk)
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import config as cfg_mod

# numpy is only needed for the optional OpenAI-embeddings path; import lazily so the
# default (pure-python TF-IDF) build doesn't require it. See _retrieve_openai_sims.
try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

CONFIG_DIR = cfg_mod.DATA_DIR
INDEX_PATH = CONFIG_DIR / "kb.jsonl"
OPENAI_VEC_PATH = CONFIG_DIR / "kb_openai_vecs.npy"
OPENAI_BATCH_SIZE = 100  # embeddings per API call

CHUNK_TARGET_CHARS = 1800  # ~450 tokens
CHUNK_OVERLAP_CHARS = 200

_LOCK = threading.RLock()


@dataclass
class Chunk:
    text: str
    source: str        # original filename or transcript title
    doc_id: str        # stable ID per uploaded item
    chunk_index: int
    score: float       # cosine similarity (higher = better; 0 to 1)
    kind: str = "document"  # 'document' | 'transcript'


# In-memory index — list of dicts; rebuild TF-IDF when _dirty=True
_records: list[dict] = []
_idf: dict[str, float] = {}          # term -> inverse document frequency
_doc_vecs: list[dict[str, float]] = []  # per-chunk L2-normalized tf-idf sparse vectors
_openai_vecs = None  # numpy float32 matrix (n_chunks, dim) when openai backend active
_openai_model_loaded: str | None = None  # which model the vecs were built with
_dirty_tfidf = True
_dirty_openai = True
_loaded = False

# --- Pure-python TF-IDF (no sklearn/scipy) ---
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset((
    "a an the of to and or in on for with at by from as is are was were be been being "
    "this that these those it its it's they them their there here we you i he she him her "
    "our your my me us do does did done have has had will would can could should may might "
    "not no nor so than too very just but if then else when while which who whom whose what "
    "how why where about into over under again more most some such only own same other "
    "up down out off above below between also any each few all both because"
).split())


def _tokenize(text: str) -> list[str]:
    """Lowercase unigrams + bigrams, stopwords and 1-char tokens removed."""
    toks = [t for t in _TOKEN_RE.findall(text.lower()) if len(t) > 1 and t not in _STOPWORDS]
    grams = list(toks)
    grams.extend(f"{toks[i]}_{toks[i + 1]}" for i in range(len(toks) - 1))
    return grams


def _vectorize(tokens: list[str]) -> dict[str, float]:
    """Sublinear-TF * IDF, restricted to known vocab, L2-normalized sparse dict."""
    if not tokens:
        return {}
    tf = Counter(tokens)
    vec: dict[str, float] = {}
    for term, freq in tf.items():
        idf = _idf.get(term)
        if idf is None:
            continue
        vec[term] = (1.0 + math.log(freq)) * idf
    norm = math.sqrt(sum(w * w for w in vec.values()))
    if norm:
        for term in vec:
            vec[term] /= norm
    return vec


def _load_from_disk() -> None:
    global _records, _dirty_tfidf, _dirty_openai, _loaded, _openai_vecs
    _records = []
    if INDEX_PATH.exists():
        with INDEX_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    _records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    if np is not None and OPENAI_VEC_PATH.exists():
        try:
            _openai_vecs = np.load(OPENAI_VEC_PATH)
            if len(_openai_vecs) != len(_records):
                _openai_vecs = None
        except Exception:
            _openai_vecs = None
    _dirty_tfidf = True
    _dirty_openai = _openai_vecs is None
    _loaded = True


def _save_to_disk() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_PATH.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for rec in _records:
            f.write(json.dumps(rec, ensure_ascii=False))
            f.write("\n")
    tmp.replace(INDEX_PATH)


def _save_openai_vecs() -> None:
    if _openai_vecs is None or len(_openai_vecs) == 0:
        if OPENAI_VEC_PATH.exists():
            OPENAI_VEC_PATH.unlink()
        return
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    np.save(OPENAI_VEC_PATH, _openai_vecs)


def _rebuild_tfidf() -> None:
    global _idf, _doc_vecs, _dirty_tfidf
    if not _records:
        _idf = {}
        _doc_vecs = []
        _dirty_tfidf = False
        return
    docs_tokens = [_tokenize(r["text"]) for r in _records]
    n = len(docs_tokens)
    df: Counter = Counter()
    for toks in docs_tokens:
        df.update(set(toks))
    # smoothed idf (matches sklearn's sublinear_tf spirit: log((1+N)/(1+df)) + 1)
    _idf = {term: math.log((1 + n) / (1 + d)) + 1.0 for term, d in df.items()}
    _doc_vecs = [_vectorize(toks) for toks in docs_tokens]
    _dirty_tfidf = False


def _openai_embed_batch(texts: list[str], model: str):
    if np is None:
        raise RuntimeError("OpenAI embeddings need numpy, which isn't available in this build")
    from openai import OpenAI
    key = cfg_mod.get_openai_key()
    if not key:
        raise RuntimeError("OpenAI API key not configured (Settings → API keys)")
    client = OpenAI(api_key=key)
    out: list[list[float]] = []
    for start in range(0, len(texts), OPENAI_BATCH_SIZE):
        batch = texts[start:start + OPENAI_BATCH_SIZE]
        resp = client.embeddings.create(model=model, input=batch)
        out.extend(d.embedding for d in resp.data)
    return np.array(out, dtype=np.float32)


def _rebuild_openai() -> None:
    """Embed any chunks that don't have a vector yet. If model changed, re-embed all."""
    global _openai_vecs, _openai_model_loaded, _dirty_openai
    cfg = cfg_mod.get_config()
    model = cfg["embeddings"].get("openai_model", "text-embedding-3-small")
    if not _records:
        _openai_vecs = None
        _openai_model_loaded = model
        _dirty_openai = False
        return
    # Full rebuild if the model changed or vec count mismatch
    if (_openai_vecs is None or len(_openai_vecs) != len(_records)
            or _openai_model_loaded != model):
        texts = [r["text"] for r in _records]
        _openai_vecs = _openai_embed_batch(texts, model)
        _openai_model_loaded = model
        _save_openai_vecs()
    _dirty_openai = False


def _l2_normalize(m):
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


def _ensure_loaded() -> None:
    if not _loaded:
        _load_from_disk()


def _read_pdf(path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n\n".join(parts)


def _read_docx(path: Path) -> str:
    from docx import Document
    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs)


def _read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _load_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _read_pdf(path)
    if suffix == ".docx":
        return _read_docx(path)
    if suffix in {".txt", ".md", ".markdown", ".rst", ".log", ".vtt", ".srt"}:
        return _read_text(path)
    raise ValueError(f"Unsupported file type: {suffix}")


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _chunk(text: str) -> list[str]:
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for para in paragraphs:
        if len(para) > CHUNK_TARGET_CHARS:
            sentences = re.split(r"(?<=[.!?])\s+", para)
            for s in sentences:
                if len(cur) + len(s) + 1 > CHUNK_TARGET_CHARS and cur:
                    chunks.append(cur.strip())
                    cur = (cur[-CHUNK_OVERLAP_CHARS:] + " " + s) if CHUNK_OVERLAP_CHARS else s
                else:
                    cur = (cur + " " + s).strip() if cur else s
            continue
        if len(cur) + len(para) + 2 > CHUNK_TARGET_CHARS and cur:
            chunks.append(cur.strip())
            cur = (cur[-CHUNK_OVERLAP_CHARS:] + "\n\n" + para) if CHUNK_OVERLAP_CHARS else para
        else:
            cur = (cur + "\n\n" + para).strip() if cur else para
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


def _doc_id_from(path: Path, content: str) -> str:
    h = hashlib.sha256()
    h.update(str(path.resolve()).encode("utf-8"))
    h.update(b"\x00")
    h.update(content.encode("utf-8")[:8192])
    return h.hexdigest()[:16]


def add_document(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    raw = _normalize(_load_text(p))
    if not raw:
        raise ValueError(f"No text extracted from {p.name}")

    doc_id = _doc_id_from(p, raw)
    with _LOCK:
        _ensure_loaded()
        if any(r["doc_id"] == doc_id for r in _records):
            return {
                "doc_id": doc_id,
                "source": p.name,
                "chunks": 0,
                "already_indexed": True,
            }

        chunks = _chunk(raw)
        if not chunks:
            raise ValueError(f"Document is empty after chunking: {p.name}")

        added_at = datetime.now().isoformat(timespec="seconds")
        for i, ch in enumerate(chunks):
            _records.append({
                "id": f"{doc_id}:{i}",
                "doc_id": doc_id,
                "source": p.name,
                "source_path": str(p.resolve()),
                "chunk_index": i,
                "added_at": added_at,
                "kind": "document",
                "text": ch,
            })
        _save_to_disk()
        global _dirty_tfidf, _dirty_openai
        _dirty_tfidf = True
        _dirty_openai = True

    return {
        "doc_id": doc_id,
        "source": p.name,
        "chunks": len(chunks),
        "already_indexed": False,
        "chars": len(raw),
    }


def index_transcript(text: str, title: str) -> dict:
    raw = _normalize(text)
    if not raw:
        return {"doc_id": "", "source": title, "chunks": 0, "already_indexed": False}

    h = hashlib.sha256()
    h.update(title.encode("utf-8"))
    h.update(b"\x00")
    h.update(raw.encode("utf-8")[:8192])
    doc_id = h.hexdigest()[:16]

    with _LOCK:
        _ensure_loaded()
        if any(r["doc_id"] == doc_id for r in _records):
            return {"doc_id": doc_id, "source": title, "chunks": 0, "already_indexed": True}

        chunks = _chunk(raw)
        if not chunks:
            return {"doc_id": doc_id, "source": title, "chunks": 0, "already_indexed": False}

        added_at = datetime.now().isoformat(timespec="seconds")
        for i, ch in enumerate(chunks):
            _records.append({
                "id": f"{doc_id}:{i}",
                "doc_id": doc_id,
                "source": title,
                "chunk_index": i,
                "added_at": added_at,
                "kind": "transcript",
                "text": ch,
            })
        _save_to_disk()
        global _dirty_tfidf, _dirty_openai
        _dirty_tfidf = True
        _dirty_openai = True

    return {
        "doc_id": doc_id,
        "source": title,
        "chunks": len(chunks),
        "already_indexed": False,
        "chars": len(raw),
    }


def retrieve(query: str, k: int = 6, kind: str | None = None) -> list[Chunk]:
    if not query.strip():
        return []
    cfg = cfg_mod.get_config()
    backend = cfg["embeddings"].get("backend", "tfidf")
    with _LOCK:
        _ensure_loaded()
        if not _records:
            return []
        if backend == "openai" and cfg_mod.get_openai_key():
            sims = _retrieve_openai_sims(query)
        else:
            sims = _retrieve_tfidf_sims(query)
        if sims is None:
            return []

        candidate_indices = list(range(len(_records)))
        if kind:
            candidate_indices = [i for i in candidate_indices if _records[i].get("kind") == kind]
        if not candidate_indices:
            return []
        candidate_sims = [(i, float(sims[i])) for i in candidate_indices]
        candidate_sims.sort(key=lambda x: x[1], reverse=True)
        top = candidate_sims[: min(k, len(candidate_sims))]
        results: list[Chunk] = []
        for idx, score in top:
            if score <= 0:
                continue
            r = _records[idx]
            results.append(Chunk(
                text=r["text"],
                source=r.get("source", "unknown"),
                doc_id=r.get("doc_id", ""),
                chunk_index=r.get("chunk_index", 0),
                score=score,
                kind=r.get("kind", "document"),
            ))
    return results


def _retrieve_tfidf_sims(query: str):
    if _dirty_tfidf:
        _rebuild_tfidf()
    if not _doc_vecs:
        return None
    q_vec = _vectorize(_tokenize(query))
    if not q_vec:
        return [0.0] * len(_records)
    sims: list[float] = []
    for dv in _doc_vecs:
        # cosine sim of two L2-normalized sparse vectors = dot product; iterate the smaller
        if len(q_vec) <= len(dv):
            s = 0.0
            for term, w in q_vec.items():
                dw = dv.get(term)
                if dw is not None:
                    s += w * dw
        else:
            s = 0.0
            for term, w in dv.items():
                qw = q_vec.get(term)
                if qw is not None:
                    s += w * qw
        sims.append(s)
    return sims


def _retrieve_openai_sims(query: str):
    cfg = cfg_mod.get_config()
    model = cfg["embeddings"].get("openai_model", "text-embedding-3-small")
    global _dirty_openai
    if _dirty_openai or _openai_vecs is None:
        _rebuild_openai()
    if _openai_vecs is None or len(_openai_vecs) == 0:
        return None
    q_arr = _openai_embed_batch([query], model)
    sims = _l2_normalize(q_arr) @ _l2_normalize(_openai_vecs).T
    return sims[0]


def list_documents() -> list[dict]:
    with _LOCK:
        _ensure_loaded()
        if not _records:
            return []
        by_doc: dict[str, dict] = {}
        for r in _records:
            did = r.get("doc_id", "?")
            info = by_doc.setdefault(did, {
                "doc_id": did,
                "source": r.get("source", "unknown"),
                "added_at": r.get("added_at", ""),
                "kind": r.get("kind", "document"),
                "chunks": 0,
            })
            info["chunks"] += 1
    return sorted(by_doc.values(), key=lambda d: d.get("added_at", ""), reverse=True)


def delete_document(doc_id: str) -> int:
    global _openai_vecs, _dirty_tfidf, _dirty_openai
    with _LOCK:
        _ensure_loaded()
        before = len(_records)
        keep_indices = [i for i, r in enumerate(_records) if r.get("doc_id") != doc_id]
        _records[:] = [_records[i] for i in keep_indices]
        removed = before - len(_records)
        if removed:
            _save_to_disk()
            _dirty_tfidf = True
            if _openai_vecs is not None:
                _openai_vecs = _openai_vecs[keep_indices] if keep_indices else None
                _save_openai_vecs()
                _dirty_openai = _openai_vecs is None
    return removed


def reset() -> None:
    global _records, _idf, _doc_vecs, _openai_vecs, _dirty_tfidf, _dirty_openai, _loaded
    with _LOCK:
        _records = []
        _idf = {}
        _doc_vecs = []
        _openai_vecs = None
        _dirty_tfidf = True
        _dirty_openai = True
        _loaded = True
        if INDEX_PATH.exists():
            INDEX_PATH.unlink()
        if OPENAI_VEC_PATH.exists():
            OPENAI_VEC_PATH.unlink()


def warm_up() -> None:
    """Load the index from disk. Cheap unless openai backend needs to embed new chunks."""
    with _LOCK:
        _ensure_loaded()


def list_chunks() -> list[Chunk]:
    """Return ALL chunks currently in the KB as Chunk objects (score=0).
    Used by the 'stuff-everything' context strategy."""
    out: list[Chunk] = []
    with _LOCK:
        _ensure_loaded()
        for r in _records:
            out.append(Chunk(
                text=r.get("text", ""),
                source=r.get("source", "unknown"),
                doc_id=r.get("doc_id", ""),
                chunk_index=r.get("chunk_index", 0),
                score=0.0,
                kind=r.get("kind", "document"),
            ))
    return out


def get_stats() -> dict:
    """Diagnostic info for the Settings window."""
    with _LOCK:
        _ensure_loaded()
        return {
            "total_chunks": len(_records),
            "openai_vecs_loaded": _openai_vecs is not None,
            "openai_vec_dim": int(_openai_vecs.shape[1]) if _openai_vecs is not None else 0,
            "openai_model_loaded": _openai_model_loaded,
        }

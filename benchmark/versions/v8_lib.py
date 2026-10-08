#!/usr/bin/env python3
"""
v8_lib.py — Shared utilities for v8 RAG techniques.

Provides:
- NLI via KIron Cross-Encoder API (nli-deberta-v3-base on GPU)
- Re-ranking via KIron API (ms-marco-MiniLM-L-6-v2 on GPU)
- Sentence extraction from markdown wiki pages
- Text chunking with overlap
- TF-IDF retrieval for chunk matching
- Wiki page and source loading
"""
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

# ── KIron Cross-Encoder API ─────────────────────────────────

KIRON_URL = os.environ.get("KIRON_URL", "http://10.0.12.16:11434")

# Local fallback model (CPU, slow but works offline)
_local_nli_model = None
_LOCAL_NLI_PATH = os.environ.get("NLI_MODEL", "/root/.cache/nli-model")


def kiron_available() -> bool:
    """Check if KIron API is reachable (fast check, 3s timeout)."""
    try:
        req = urllib.request.Request(f"{KIRON_URL}/api/score",
                                     data=json.dumps({"model": "nli-deberta-v3-base",
                                                       "pairs": [["test", "test"]]}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
        return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def _load_local_nli():
    """Fallback: load NLI model locally on CPU."""
    global _local_nli_model
    if _local_nli_model is None:
        from sentence_transformers import CrossEncoder
        path = _LOCAL_NLI_PATH if Path(_LOCAL_NLI_PATH).exists() else "cross-encoder/nli-deberta-v3-base"
        print(f"  ⚠ KIron nicht erreichbar — lade lokales Modell ({path})...", file=sys.stderr, flush=True)
        _local_nli_model = CrossEncoder(path)
        print(f"  ✓ Lokales Modell geladen (CPU, langsam)", file=sys.stderr, flush=True)
    return _local_nli_model


# ── Markdown parsing ────────────────────────────────────────

def extract_body(md_text: str) -> str:
    """Strip YAML frontmatter from markdown, return body text."""
    lines = md_text.split("\n")
    in_fm = False
    fm_count = 0
    body_start = 0
    for i, line in enumerate(lines):
        if line.strip() == "---":
            fm_count += 1
            if fm_count == 1:
                in_fm = True
            elif fm_count == 2:
                in_fm = False
                body_start = i + 1
                break
    if fm_count < 2:
        body_start = 0
    return "\n".join(lines[body_start:])


def extract_sentences(md_text: str) -> list[str]:
    """Extract factual sentences from a wiki page body.

    Skips: frontmatter, headers, code blocks, pure wikilink lists,
    table dividers, blockquote definitions.
    """
    body = extract_body(md_text)
    lines = body.split("\n")

    # Remove code blocks
    filtered = []
    in_code = False
    for line in lines:
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if not in_code:
            filtered.append(line)

    # Filter out non-content lines
    prose_lines = []
    for line in filtered:
        stripped = line.strip()
        # Skip headers
        if stripped.startswith("#"):
            continue
        # Skip table dividers
        if re.match(r"^\|[-:\s|]+\|$", stripped):
            continue
        # Skip empty lines (paragraph separator)
        if not stripped:
            if prose_lines and prose_lines[-1] != "":
                prose_lines.append("")
            continue
        # Skip pure wikilink list items: "- [[Page Name]]" or "- [[Page Name]] — description"
        if re.match(r"^[-*]\s+\[\[[^\]]+\]\]\s*($|—)", stripped):
            continue
        # Skip blockquote definitions (> **Kurzdefinition** ...)
        if stripped.startswith("> **") and len(stripped) < 120:
            continue
        prose_lines.append(stripped)

    # Join into paragraphs and split into sentences
    text = " ".join(line for line in prose_lines if line)
    # Clean markdown artifacts
    text = re.sub(r"\[\[([^\]|]+)(\|[^\]]+)?\]\]", r"\1", text)  # [[X|Y]] → X
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)  # **bold** → bold
    text = re.sub(r"\*([^*]+)\*", r"\1", text)  # *italic* → italic
    text = re.sub(r"`([^`]+)`", r"\1", text)  # `code` → code

    # Sentence splitting — handle German abbreviations
    # Protect common abbreviations
    text = re.sub(r"\bz\.B\.", "z·B·", text)
    text = re.sub(r"\bd\.h\.", "d·h·", text)
    text = re.sub(r"\bu\.a\.", "u·a·", text)
    text = re.sub(r"\bbzw\.", "bzw·", text)
    text = re.sub(r"\bvgl\.", "vgl·", text)
    text = re.sub(r"\bggf\.", "ggf·", text)
    text = re.sub(r"\bi\.d\.R\.", "i·d·R·", text)
    text = re.sub(r"\bS\.\s*(\d)", r"S· \1", text)

    # Split on sentence boundaries
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ])", text)

    # Restore abbreviations and filter
    result = []
    for s in sentences:
        s = s.replace("·", ".").strip()
        # Remove trailing/leading list markers
        s = re.sub(r"^[-*•]\s+", "", s)
        # Skip very short or non-factual
        if len(s) < 20:
            continue
        # Skip if it's just a table row
        if s.count("|") > 2:
            continue
        result.append(s)

    return result


# ── Text chunking ───────────────────────────────────────────

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    """Split text into overlapping chunks by character count."""
    text = text.strip()
    if len(text) <= chunk_size:
        return [text] if text else []

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        # Try to break at a sentence boundary
        if end < len(text):
            # Look for a period or newline near the end
            for sep in [". ", ".\n", "\n\n", "\n", " "]:
                idx = text.rfind(sep, start + chunk_size // 2, end + 50)
                if idx > start:
                    end = idx + len(sep)
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap

    return chunks


# ── Simple word-overlap retrieval ───────────────────────────

def _tokenize(text: str) -> set[str]:
    """Simple word tokenization: lowercase, alphanumeric tokens ≥ 3 chars."""
    return {w for w in re.findall(r"[a-zäöüß0-9]{3,}", text.lower())}


def jaccard_similarity(a: str, b: str) -> float:
    """Jaccard similarity between two texts (word-level)."""
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


class TFIDFIndex:
    """Simple TF-IDF index for fast chunk retrieval."""

    def __init__(self, documents: list[str]):
        self.documents = documents
        self.n = len(documents)
        if self.n == 0:
            self.vocab = []
            return

        # Tokenize
        doc_tokens = [_tokenize(d) for d in documents]

        # Build vocabulary and IDF
        all_tokens = set()
        for tokens in doc_tokens:
            all_tokens |= tokens
        self.vocab = sorted(all_tokens)
        vocab_idx = {w: i for i, w in enumerate(self.vocab)}

        # TF matrix (n_docs x n_vocab)
        self._tf = np.zeros((self.n, len(self.vocab)), dtype=np.float32)
        for i, tokens in enumerate(doc_tokens):
            for token in tokens:
                if token in vocab_idx:
                    self._tf[i, vocab_idx[token]] += 1.0
            # Normalize TF by document length
            if tokens:
                self._tf[i] /= len(tokens)

        # IDF
        df = (self._tf > 0).sum(axis=0) + 1.0
        self._idf = np.log(self.n / df + 1.0)

        # TF-IDF vectors
        self._tfidf = self._tf * self._idf

        # Pre-normalize for cosine similarity
        norms = np.linalg.norm(self._tfidf, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self._normed = self._tfidf / norms

        self._vocab_idx = vocab_idx

    def query(self, text: str, top_k: int = 5) -> list[tuple[int, float]]:
        """Find top-k most similar documents to query text.
        Returns list of (doc_index, score).
        """
        if not self.vocab:
            return []

        tokens = _tokenize(text)
        q = np.zeros(len(self.vocab), dtype=np.float32)
        for token in tokens:
            if token in self._vocab_idx:
                q[self._vocab_idx[token]] = 1.0
        if tokens:
            q /= len(tokens)
        q *= self._idf
        norm = np.linalg.norm(q)
        if norm == 0:
            return []
        q /= norm

        scores = self._normed @ q
        top_indices = np.argsort(-scores)[:top_k]
        return [(int(i), float(scores[i])) for i in top_indices if scores[i] > 0]


def find_best_chunks(sentence: str, chunks: list[str], top_k: int = 3,
                     tfidf_index: TFIDFIndex | None = None) -> list[tuple[str, float]]:
    """Find the top-k chunks most similar to a sentence.

    Uses TF-IDF index if provided, otherwise falls back to Jaccard.
    """
    if tfidf_index is not None:
        results = tfidf_index.query(sentence, top_k=top_k)
        return [(chunks[i], score) for i, score in results]

    # Fallback: Jaccard
    scored = [(c, jaccard_similarity(sentence, c)) for c in chunks]
    scored.sort(key=lambda x: -x[1])
    return scored[:top_k]


# ── NLI via KIron API ───────────────────────────────────────


def _kiron_score(pairs: list[list[str]], model: str, batch_size: int = 256) -> list[dict]:
    """Call KIron /api/score endpoint. Returns raw result dicts.
    Raises ConnectionError if KIron is unreachable.
    """
    url = f"{KIRON_URL}/api/score"
    all_results = []

    for i in range(0, len(pairs), batch_size):
        batch = pairs[i:i + batch_size]
        payload = json.dumps({"model": model, "pairs": batch}).encode("utf-8")
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            all_results.extend(body.get("results", []))
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise ConnectionError(f"KIron API nicht erreichbar ({KIRON_URL}): {e}") from e

    return all_results


def _kiron_rerank(query: str, documents: list[str], model: str = "ms-marco-MiniLM-L-6-v2",
                  top_k: int | None = None) -> list[dict]:
    """Call KIron /api/rerank endpoint. Returns sorted result dicts."""
    url = f"{KIRON_URL}/api/rerank"
    payload = {"model": model, "query": query, "documents": documents}
    if top_k is not None:
        payload["top_k"] = top_k
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body.get("results", [])
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise ConnectionError(f"KIron API nicht erreichbar ({KIRON_URL}): {e}") from e


def _nli_predict_local(pairs: list[tuple[str, str]], batch_size: int = 32) -> list[dict]:
    """Fallback: NLI via local CPU model."""
    model = _load_local_nli()

    all_logits = []
    for i in range(0, len(pairs), batch_size):
        batch = pairs[i:i + batch_size]
        logits = model.predict(batch)
        if logits.ndim == 1:
            logits = logits.reshape(1, -1)
        all_logits.append(logits)

    logits = np.vstack(all_logits)
    # Softmax
    e = np.exp(logits - logits.max(axis=1, keepdims=True))
    probs = e / e.sum(axis=1, keepdims=True)

    labels_map = {0: "contradiction", 1: "entailment", 2: "neutral"}
    results = []
    for row in probs:
        best = int(np.argmax(row))
        results.append({
            "label": labels_map[best],
            "scores": {"contradiction": float(row[0]), "entailment": float(row[1]),
                        "neutral": float(row[2])},
        })
    return results


def nli_predict(pairs: list[tuple[str, str]], batch_size: int = 256) -> list[dict]:
    """Run NLI prediction on (premise, hypothesis) pairs.

    Tries KIron API first (GPU, fast). Falls back to local CPU model if
    KIron is unreachable and a local model exists.

    Returns list of dicts with keys:
    - label: 'entailment', 'neutral', or 'contradiction'
    - scores: {entailment: float, neutral: float, contradiction: float}
    """
    if not pairs:
        return []

    # Try KIron API first
    try:
        api_pairs = [[a, b] for a, b in pairs]
        raw = _kiron_score(api_pairs, model="nli-deberta-v3-base", batch_size=batch_size)

        results = []
        for r in raw:
            ent = r.get("entailment", 0.0)
            con = r.get("contradiction", 0.0)
            neu = r.get("neutral", 0.0)
            best = max(("entailment", ent), ("contradiction", con), ("neutral", neu),
                       key=lambda x: x[1])
            results.append({
                "label": best[0],
                "scores": {"entailment": ent, "contradiction": con, "neutral": neu},
            })
        return results

    except ConnectionError as e:
        print(f"  ⚠ {e}", file=sys.stderr, flush=True)

        # Fallback: local model
        if Path(_LOCAL_NLI_PATH).exists():
            print(f"  → Fallback auf lokales Modell (CPU, langsam)...",
                  file=sys.stderr, flush=True)
            return _nli_predict_local(pairs, batch_size=32)

        print(f"  ✗ Kein lokales Modell unter {_LOCAL_NLI_PATH} — Abbruch.",
              file=sys.stderr, flush=True)
        raise


def rerank(query: str, documents: list[str], top_k: int | None = None) -> list[dict]:
    """Re-rank documents via KIron API.

    Returns list of dicts with keys: index, score, document (sorted by score desc).
    """
    try:
        return _kiron_rerank(query, documents, top_k=top_k)
    except ConnectionError as e:
        print(f"  ⚠ Re-Ranking nicht verfügbar: {e}", file=sys.stderr, flush=True)
        return []


# ── File loading ────────────────────────────────────────────

def load_pages(vault: str | Path) -> dict[str, str]:
    """Load all wiki pages from a vault. Returns {relative_path: content}."""
    vault = Path(vault)
    wiki = vault / "wiki"
    if not wiki.exists():
        return {}
    pages = {}
    for p in wiki.rglob("*.md"):
        rel = str(p.relative_to(vault))
        pages[rel] = p.read_text(encoding="utf-8", errors="replace")
    return pages


def load_sources(sources_dir: str | Path) -> dict[str, str]:
    """Load all source documents. Returns {filename: content}."""
    sources_dir = Path(sources_dir)
    sources = {}
    for p in sorted(sources_dir.glob("*.md")):
        sources[p.name] = p.read_text(encoding="utf-8", errors="replace")
    return sources

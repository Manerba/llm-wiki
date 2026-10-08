#!/usr/bin/env python3
"""
v8-contradictions.py — NLI-basierte Widerspruchserkennung

Findet Widersprüche zwischen Wiki-Pages: paarweise NLI-Prüfung der
Kernaussagen semantisch ähnlicher Pages.

Verwendet TF-IDF für Ähnlichkeitssuche (kein qmd nötig) und
cross-encoder/nli-deberta-v3-base für Widerspruchserkennung.

Usage:
    VAULT=/opt/llm-wiki-bench-opus-agent \\
    TAG=opus-agent \\
    python3 v8-contradictions.py

    # Optional: qmd für Ähnlichkeitssuche nutzen
    USE_QMD=1 python3 v8-contradictions.py

Output:
    results/<tag>-v8-contradictions.tsv
"""
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from v8_lib import (
    load_pages, extract_sentences,
    nli_predict, _tokenize,
)

VAULT = Path(os.environ.get("VAULT", "/opt/llm-wiki-bench-opus-agent"))
TAG = os.environ.get("TAG", "v8-test")
RESULTS_DIR = Path(os.environ.get("RESULTS", "/opt/llm-wiki/benchmark/results"))
USE_QMD = os.environ.get("USE_QMD", "0") == "1"

THRESHOLD_CONTRADICTION = float(os.environ.get("THRESHOLD_CONTRADICTION", "0.8"))
TOP_K_SIMILAR = int(os.environ.get("TOP_K_SIMILAR", "5"))


def tfidf_similarity(pages: dict[str, list[str]]) -> dict[str, list[str]]:
    """Find top-K similar page pairs using simple TF-IDF cosine similarity."""
    page_names = list(pages.keys())
    n = len(page_names)

    # Build vocabulary
    vocab = set()
    page_tokens = {}
    for name in page_names:
        tokens = set()
        for sent in pages[name]:
            tokens |= _tokenize(sent)
        page_tokens[name] = tokens
        vocab |= tokens

    vocab = sorted(vocab)
    vocab_idx = {w: i for i, w in enumerate(vocab)}

    # Build TF vectors
    vectors = np.zeros((n, len(vocab)), dtype=np.float32)
    for i, name in enumerate(page_names):
        for token in page_tokens[name]:
            if token in vocab_idx:
                vectors[i, vocab_idx[token]] = 1.0

    # IDF
    df = (vectors > 0).sum(axis=0) + 1
    idf = np.log(n / df)
    vectors *= idf

    # Cosine similarity
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    normed = vectors / norms
    sim_matrix = normed @ normed.T

    # For each page, find top-K similar (excluding self)
    similar = {}
    for i, name in enumerate(page_names):
        sims = list(enumerate(sim_matrix[i]))
        sims.sort(key=lambda x: -x[1])
        top = [page_names[j] for j, s in sims if j != i and s > 0.1][:TOP_K_SIMILAR]
        similar[name] = top

    return similar


def qmd_similar(page_name: str) -> list[str]:
    """Use qmd vsearch to find similar pages."""
    try:
        result = subprocess.run(
            ["qmd", "vsearch", page_name, "-n", str(TOP_K_SIMILAR)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return []
        # Parse qmd output: each line is a result
        pages = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            # qmd output format varies — extract file path
            parts = line.strip().split()
            if parts:
                name = parts[-1] if "/" in parts[-1] else parts[0]
                name = Path(name).stem
                pages.append(name)
        return pages
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def main():
    print(f"v8 Widerspruchserkennung")
    print(f"  Vault: {VAULT}")
    print(f"  Tag:   {TAG}")
    print(f"  Modus: {'qmd vsearch' if USE_QMD else 'TF-IDF'}")
    print()

    from v8_lib import KIRON_URL, kiron_available
    api_ok = kiron_available()
    print(f"  KIron API: {KIRON_URL} ({'✓ erreichbar' if api_ok else '⚠ nicht erreichbar — CPU-Fallback'})")

    # Load pages and extract sentences
    raw_pages = load_pages(VAULT)
    page_sentences = {}
    for rel_path, content in raw_pages.items():
        sents = extract_sentences(content)
        if sents:
            page_sentences[rel_path] = sents
    print(f"  Pages mit Sätzen: {len(page_sentences)}")

    # Find similar page pairs
    print("  Suche ähnliche Page-Paare...")
    if USE_QMD:
        similar = {}
        for rel_path in page_sentences:
            page_name = Path(rel_path).stem
            sim = qmd_similar(page_name)
            # Map back to rel_paths
            name_to_rel = {Path(r).stem: r for r in page_sentences}
            similar[rel_path] = [name_to_rel[s] for s in sim if s in name_to_rel]
    else:
        similar = tfidf_similarity(page_sentences)
        # Map from names to rel_paths if needed
        name_to_rel = {Path(r).stem: r for r in page_sentences}
        mapped = {}
        for name, sims in similar.items():
            if name in name_to_rel:
                mapped[name_to_rel[name]] = [name_to_rel.get(s, s) for s in sims if s in name_to_rel or s in page_sentences]
            elif name in page_sentences:
                mapped[name] = [s for s in sims if s in page_sentences]
        similar = mapped

    total_pairs = sum(len(v) for v in similar.values())
    print(f"  Paare zu prüfen: {total_pairs}")

    # Check contradictions
    print("  Prüfe Widersprüche...")
    contradictions = []
    checked_pairs = set()
    pair_count = 0

    for page_a, similars in sorted(similar.items()):
        for page_b in similars:
            # Avoid duplicate checks
            pair_key = tuple(sorted([page_a, page_b]))
            if pair_key in checked_pairs:
                continue
            checked_pairs.add(pair_key)
            pair_count += 1

            sents_a = page_sentences.get(page_a, [])
            sents_b = page_sentences.get(page_b, [])

            if not sents_a or not sents_b:
                continue

            # Limit: max 10 sentences per page to keep runtime manageable
            sents_a = sents_a[:10]
            sents_b = sents_b[:10]

            # Build all (a, b) pairs for NLI
            pairs = []
            pair_indices = []
            for ia, sa in enumerate(sents_a):
                for ib, sb in enumerate(sents_b):
                    pairs.append((sa, sb))
                    pair_indices.append((ia, ib))

            if not pairs:
                continue

            results = nli_predict(pairs, batch_size=256)

            for (ia, ib), res in zip(pair_indices, results):
                if res["scores"]["contradiction"] >= THRESHOLD_CONTRADICTION:
                    contradictions.append({
                        "page_a": page_a,
                        "page_b": page_b,
                        "sentence_a": sents_a[ia],
                        "sentence_b": sents_b[ib],
                        "score": res["scores"]["contradiction"],
                    })

    # Write report
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_tsv = RESULTS_DIR / f"{TAG}-v8-contradictions.tsv"

    with open(out_tsv, "w") as f:
        f.write("page_a\tpage_b\tsentence_a\tsentence_b\tscore\n")
        for c in sorted(contradictions, key=lambda x: -x["score"]):
            sa = c["sentence_a"][:150].replace("\t", " ").replace("\n", " ")
            sb = c["sentence_b"][:150].replace("\t", " ").replace("\n", " ")
            f.write(f"{c['page_a']}\t{c['page_b']}\t{sa}\t{sb}\t{c['score']:.3f}\n")

    print()
    print(f"  {'='*50}")
    print(f"  Geprüfte Paare: {len(checked_pairs)}")
    print(f"  Widersprüche gefunden: {len(contradictions)}")
    if contradictions:
        print(f"  Top-Widersprüche:")
        for c in sorted(contradictions, key=lambda x: -x["score"])[:5]:
            print(f"    {Path(c['page_a']).stem} ↔ {Path(c['page_b']).stem} ({c['score']:.2f})")
            print(f"      A: {c['sentence_a'][:80]}...")
            print(f"      B: {c['sentence_b'][:80]}...")
    print(f"  Report: {out_tsv}")


if __name__ == "__main__":
    main()

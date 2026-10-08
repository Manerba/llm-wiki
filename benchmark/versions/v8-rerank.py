#!/usr/bin/env python3
"""
v8-rerank.py — Cross-Encoder Re-Ranking für Wiki-Queries

Wrapper um qmd search: holt Top-20 Treffer, re-rankt mit Cross-Encoder,
liefert Top-K zurück.

Usage:
    # Als CLI-Tool:
    python3 v8-rerank.py "Was ist SLAAC?"
    python3 v8-rerank.py "IPv6 Firewall Regeln" --top-k 10 --collection wiki

    # Als Modul:
    from v8_rerank import rerank_query
    results = rerank_query("Was ist SLAAC?", top_k=5)

Output:
    Gerankte Ergebnisse auf stdout (score, path, snippet)
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from v8_lib import rerank


def qmd_search(query: str, n: int = 20, collection: str | None = None) -> list[dict]:
    """Run qmd search and parse results."""
    cmd = ["qmd", "search", query, "-n", str(n)]
    if collection:
        cmd.extend(["--collection", collection])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"  qmd search error: {result.stderr[:200]}", file=sys.stderr)
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"  qmd nicht verfügbar: {e}", file=sys.stderr)
        return []

    # Parse qmd output — format: score, path, snippet per result block
    hits = []
    current = {}
    for line in result.stdout.split("\n"):
        line = line.rstrip()
        if not line:
            if current:
                hits.append(current)
                current = {}
            continue
        # Try to detect structured output
        if line.startswith("  ") and current:
            current["snippet"] = current.get("snippet", "") + " " + line.strip()
        elif ":" in line and not current.get("path"):
            # Could be "score: 0.85" or path line
            parts = line.split(None, 1)
            if parts:
                current["raw"] = line
                current["path"] = parts[-1] if "/" in parts[-1] else parts[0]
                current["snippet"] = ""

    if current:
        hits.append(current)

    return hits


def qmd_get(path: str, max_lines: int = 30) -> str:
    """Retrieve document content from qmd."""
    try:
        result = subprocess.run(
            ["qmd", "get", path, "-l", str(max_lines)],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def rerank_query(query: str, top_k: int = 5, initial_n: int = 20,
                 collection: str | None = None) -> list[dict]:
    """Search with qmd, re-rank with cross-encoder, return top-K.

    Returns list of dicts with keys: path, score, original_score, snippet
    """
    # Step 1: Initial retrieval
    hits = qmd_search(query, n=initial_n, collection=collection)
    if not hits:
        return []

    # Step 2: Load content for each hit
    for hit in hits:
        if not hit.get("snippet"):
            content = qmd_get(hit.get("path", ""), max_lines=20)
            hit["snippet"] = content[:500] if content else hit.get("raw", "")

    # Step 3: Cross-encoder re-ranking via KIron API
    documents = [hit.get("snippet", "")[:500] for hit in hits]
    if not documents:
        return []

    ranked = rerank(query, documents, top_k=top_k)

    # Step 4: Map scores back to hits
    for r in ranked:
        idx = r.get("index", 0)
        if idx < len(hits):
            hits[idx]["rerank_score"] = r.get("score", 0.0)

    reranked = sorted(hits, key=lambda x: -x.get("rerank_score", float("-inf")))
    return reranked[:top_k]


def main():
    parser = argparse.ArgumentParser(description="v8 Cross-Encoder Re-Ranking")
    parser.add_argument("query", help="Suchanfrage")
    parser.add_argument("--top-k", type=int, default=5, help="Anzahl Ergebnisse (default: 5)")
    parser.add_argument("--initial-n", type=int, default=20, help="Initiale qmd-Treffer (default: 20)")
    parser.add_argument("--collection", type=str, default=None, help="qmd Collection")
    args = parser.parse_args()

    print(f"v8 Re-Ranking: \"{args.query}\"")
    print(f"  Top-K: {args.top_k}, Initial: {args.initial_n}")
    print()

    t0 = time.time()
    results = rerank_query(args.query, top_k=args.top_k,
                           initial_n=args.initial_n, collection=args.collection)
    dt = time.time() - t0

    if not results:
        print("  Keine Ergebnisse.")
        return

    print(f"  {len(results)} Ergebnisse ({dt:.1f}s):")
    print()
    for i, r in enumerate(results):
        score = r.get("rerank_score", 0)
        path = r.get("path", "?")
        snippet = r.get("snippet", "")[:120].replace("\n", " ")
        print(f"  {i+1}. [{score:.3f}] {path}")
        print(f"     {snippet}")
        print()


if __name__ == "__main__":
    main()

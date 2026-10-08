#!/usr/bin/env python3
"""
v8-hyde.py — HyDE (Hypothetical Document Embedding)

Generiert vor dem Retrieval eine hypothetische Antwort via LLM.
Deren Embedding wird für die Vektor-Suche genutzt statt des Frage-Embeddings.

Usage:
    # Mit lokalem Modell (Ollama):
    python3 v8-hyde.py "Wie hängen Memex und Zettelkasten zusammen?"
    python3 v8-hyde.py "IPv6 Sicherheit" --model qwen3.5:latest

    # Als Modul:
    from v8_hyde import hyde_search
    results = hyde_search("Wie hängen Memex und Zettelkasten zusammen?")

Output:
    Verbesserte Suchergebnisse auf stdout
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://10.0.12.16:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:latest")


def generate_hypothesis(query: str, model: str = None, url: str = None) -> str:
    """Generate a hypothetical answer to the query using Ollama."""
    model = model or OLLAMA_MODEL
    url = url or OLLAMA_URL

    prompt = (
        f"Beantworte die folgende Frage in 2-3 Sätzen auf Deutsch. "
        f"Schreibe sachlich und enzyklopädisch, als wäre es ein Wiki-Artikel.\n\n"
        f"Frage: {query}\n\nAntwort:"
    )

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {
            "num_predict": 200,
            "temperature": 0.3,
        },
    }

    api_url = f"{url.rstrip('/')}/api/chat"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        api_url, data=data,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = body.get("message", {}).get("content", "")
        # Strip thinking tags if present (qwen3 uses <think>...</think>)
        import re
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        return content
    except Exception as e:
        print(f"  ⚠ Ollama-Fehler: {e}", file=sys.stderr)
        return query  # Fallback: use original query


def qmd_vsearch(text: str, n: int = 5, collection: str | None = None) -> list[dict]:
    """Run qmd vsearch with the given text."""
    cmd = ["qmd", "vsearch", text, "-n", str(n)]
    if collection:
        cmd.extend(["--collection", collection])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    hits = []
    current = {}
    for line in result.stdout.split("\n"):
        line = line.rstrip()
        if not line:
            if current:
                hits.append(current)
                current = {}
            continue
        if not current:
            current = {"raw": line, "path": "", "snippet": ""}
            parts = line.split(None, 1)
            if parts:
                current["path"] = parts[-1] if "/" in parts[-1] else parts[0]
        else:
            current["snippet"] = current.get("snippet", "") + " " + line.strip()

    if current:
        hits.append(current)

    return hits


def qmd_search_fallback(text: str, n: int = 5, collection: str | None = None) -> list[dict]:
    """Fallback: use keyword search if vsearch fails."""
    cmd = ["qmd", "search", text, "-n", str(n)]
    if collection:
        cmd.extend(["--collection", collection])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    hits = []
    for line in result.stdout.split("\n"):
        if line.strip():
            hits.append({"raw": line, "path": line.split()[-1] if line.split() else ""})
    return hits


def hyde_search(query: str, model: str = None, n: int = 5,
                collection: str | None = None) -> dict:
    """Full HyDE pipeline: generate hypothesis → embed → search.

    Returns dict with keys: query, hypothesis, results
    """
    # Step 1: Generate hypothetical answer
    hypothesis = generate_hypothesis(query, model=model)

    # Step 2: Search with hypothesis embedding
    results = qmd_vsearch(hypothesis, n=n, collection=collection)

    # Step 3: Fallback to keyword search if vsearch yields nothing
    if not results:
        results = qmd_search_fallback(hypothesis, n=n, collection=collection)

    return {
        "query": query,
        "hypothesis": hypothesis,
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description="v8 HyDE — Hypothetical Document Embedding")
    parser.add_argument("query", help="Suchanfrage")
    parser.add_argument("--model", default=None, help=f"Ollama-Modell (default: {OLLAMA_MODEL})")
    parser.add_argument("-n", type=int, default=5, help="Anzahl Ergebnisse (default: 5)")
    parser.add_argument("--collection", default=None, help="qmd Collection")
    args = parser.parse_args()

    print(f"v8 HyDE: \"{args.query}\"")
    print(f"  Modell: {args.model or OLLAMA_MODEL}")
    print()

    t0 = time.time()
    result = hyde_search(args.query, model=args.model, n=args.n, collection=args.collection)
    dt = time.time() - t0

    print(f"  Hypothese ({dt:.1f}s):")
    print(f"    \"{result['hypothesis'][:200]}\"")
    print()

    if result["results"]:
        print(f"  {len(result['results'])} Ergebnisse:")
        for i, r in enumerate(result["results"]):
            path = r.get("path", "?")
            snippet = r.get("snippet", r.get("raw", ""))[:120].replace("\n", " ")
            print(f"    {i+1}. {path}")
            if snippet:
                print(f"       {snippet}")
    else:
        print("  Keine Ergebnisse (qmd vsearch nicht verfügbar oder keine Embeddings?)")
        print("  Tipp: 'qmd embed' ausführen um Embeddings zu erstellen.")


if __name__ == "__main__":
    main()

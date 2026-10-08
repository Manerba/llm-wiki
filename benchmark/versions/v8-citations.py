#!/usr/bin/env python3
"""
v8-citations.py — Zitationsextraktion

Verknüpft jede Aussage in einer Wiki-Page mit der konkreten Passage
in der Originalquelle — oder markiert sie als "nicht quellenbelegt".

Baut auf dem Groundedness-Modell auf (cross-encoder/nli-deberta-v3-base).

Usage:
    VAULT=/opt/llm-wiki-bench-opus-agent \\
    SOURCES=/opt/llm-wiki/raw/converted-v5/IPv6 \\
    TAG=opus-agent \\
    python3 v8-citations.py

    # Optional: Pages mit Markierungen annotieren
    ANNOTATE=1 python3 v8-citations.py

Output:
    results/<tag>-v8-citations.tsv  (pro Satz: Quelle, Passage, Score, Status)
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from v8_lib import (
    load_pages, load_sources,
    extract_sentences, extract_body, chunk_text, find_best_chunks,
    nli_predict, TFIDFIndex,
)

VAULT = Path(os.environ.get("VAULT", "/opt/llm-wiki-bench-opus-agent"))
SOURCES_DIR = Path(os.environ.get("SOURCES", "/opt/llm-wiki/raw/converted-v5/IPv6"))
TAG = os.environ.get("TAG", "v8-test")
RESULTS_DIR = Path(os.environ.get("RESULTS", "/opt/llm-wiki/benchmark/results"))
ANNOTATE = os.environ.get("ANNOTATE", "0") == "1"

THRESHOLD_CITED = float(os.environ.get("THRESHOLD_CITED", "0.5"))


def main():
    print(f"v8 Zitationsextraktion")
    print(f"  Vault:    {VAULT}")
    print(f"  Sources:  {SOURCES_DIR}")
    print(f"  Annotate: {ANNOTATE}")
    print()

    from v8_lib import KIRON_URL, kiron_available
    api_ok = kiron_available()
    print(f"  KIron API: {KIRON_URL} ({'✓ erreichbar' if api_ok else '⚠ nicht erreichbar — CPU-Fallback'})")

    # Load and chunk sources
    sources = load_sources(SOURCES_DIR)
    source_chunks = {}  # source_name → list of chunks
    all_chunks = []     # (source_name, chunk_text)

    for name, content in sources.items():
        chunks = chunk_text(content, chunk_size=400, overlap=80)
        source_chunks[name] = chunks
        for c in chunks:
            all_chunks.append((name, c))

    chunk_texts = [c[1] for c in all_chunks]
    tfidf = TFIDFIndex(chunk_texts)
    print(f"  Quellen: {len(sources)}, Chunks: {len(all_chunks)}, TF-IDF: {len(tfidf.vocab)} Terme")

    # Load pages
    pages = load_pages(VAULT)
    print(f"  Pages: {len(pages)}")
    print()

    # Output
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_tsv = RESULTS_DIR / f"{TAG}-v8-citations.tsv"

    total_cited = 0
    total_uncited = 0
    total_sentences = 0

    with open(out_tsv, "w") as f:
        f.write("page\tsentence\tstatus\tscore\tsource\tpassage\n")

        for idx, (rel_path, content) in enumerate(sorted(pages.items())):
            sentences = extract_sentences(content)
            if not sentences:
                print(f"  [{idx+1}/{len(pages)}] — {rel_path} (keine Sätze)")
                continue

            # Build NLI pairs: for each sentence, check against top-5 chunks
            all_pairs = []
            pair_map = []

            for si, sent in enumerate(sentences):
                best = find_best_chunks(sent, chunk_texts, top_k=5, tfidf_index=tfidf)
                for ci, (chunk, _) in enumerate(best):
                    all_pairs.append((chunk, sent))
                    pair_map.append((si, ci, chunk))

            results = nli_predict(all_pairs, batch_size=256) if all_pairs else []

            # Aggregate: for each sentence, find best citation
            citations = []
            for si, sent in enumerate(sentences):
                best_ent = 0.0
                best_source = None
                best_passage = ""

                for (psi, pci, pchunk), res in zip(pair_map, results):
                    if psi != si:
                        continue
                    ent = res["scores"]["entailment"]
                    if ent > best_ent:
                        best_ent = ent
                        best_passage = pchunk
                        # Find source name
                        for sname, stext in all_chunks:
                            if stext == pchunk:
                                best_source = sname
                                break

                if best_ent >= THRESHOLD_CITED:
                    status = "quellenbelegt"
                    total_cited += 1
                else:
                    status = "nicht quellenbelegt"
                    total_uncited += 1

                total_sentences += 1
                sent_clean = sent[:200].replace("\t", " ").replace("\n", " ")
                passage_clean = best_passage[:150].replace("\t", " ").replace("\n", " ")
                f.write(f"{rel_path}\t{sent_clean}\t{status}\t{best_ent:.3f}\t{best_source or ''}\t{passage_clean}\n")

                citations.append({
                    "sentence": sent,
                    "status": status,
                    "score": best_ent,
                    "source": best_source,
                    "passage": best_passage,
                })

            cited = sum(1 for c in citations if c["status"] == "quellenbelegt")
            rate = cited / len(citations) if citations else 1.0
            status_char = "✓" if rate >= 0.7 else "⚠" if rate >= 0.4 else "✗"
            print(f"  [{idx+1}/{len(pages)}] {status_char} {rel_path}: {cited}/{len(citations)} belegt ({rate:.0%})")

            # Optional: annotate page with citation markers
            if ANNOTATE and citations:
                _annotate_page(VAULT / rel_path, content, citations)

    # Summary
    overall_rate = total_cited / total_sentences if total_sentences else 1.0
    print()
    print(f"  {'='*50}")
    print(f"  Zitationsrate: {overall_rate:.1%}")
    print(f"  {total_cited} quellenbelegt, {total_uncited} nicht quellenbelegt")
    print(f"  Report: {out_tsv}")


def _annotate_page(page_path: Path, content: str, citations: list[dict]):
    """Insert [nicht quellenbelegt] markers into page body for uncited sentences."""
    body = extract_body(content)
    modified = body
    insertions = 0

    for cit in citations:
        if cit["status"] != "nicht quellenbelegt":
            continue
        # Find the sentence in the body and add marker
        sent = cit["sentence"]
        # Try to find a substring match (sentences may have been cleaned)
        # Use first 40 chars as anchor
        anchor = sent[:40]
        # Remove markdown from anchor for matching
        import re
        anchor_clean = re.sub(r"\[\[([^\]|]+)(\|[^\]]+)?\]\]", r"\1", anchor)
        idx = body.find(anchor_clean)
        if idx >= 0:
            # Find end of sentence
            end = body.find(".", idx + len(anchor_clean))
            if end >= 0:
                insert_pos = end + 1
                if " [nicht quellenbelegt]" not in body[insert_pos:insert_pos+30]:
                    modified = modified[:insert_pos] + " [nicht quellenbelegt]" + modified[insert_pos:]
                    insertions += 1

    if insertions > 0:
        # Reconstruct full page (frontmatter + modified body)
        lines = content.split("\n")
        fm_end = 0
        fm_count = 0
        for i, line in enumerate(lines):
            if line.strip() == "---":
                fm_count += 1
                if fm_count == 2:
                    fm_end = i + 1
                    break
        frontmatter = "\n".join(lines[:fm_end])
        page_path.write_text(frontmatter + "\n" + modified, encoding="utf-8")
        print(f"    → {insertions} Markierungen eingefügt")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
v8-groundedness.py — NLI-basiertes Groundedness-Checking

Prüft jede Aussage in Wiki-Pages gegen die Originalquellen mit einem
dedizierten NLI-Modell (cross-encoder/nli-deberta-v3-base).

Ersetzt die LLM-basierte Halluzinations-Prüfung (Phase B in run-full.sh)
durch eine deterministische, modell-unabhängige Prüfung.

Optimiert für Durchsatz: TF-IDF Chunk-Retrieval + globales NLI-Batching.

Usage:
    VAULT=/opt/llm-wiki-bench-opus-agent \\
    SOURCES=/opt/llm-wiki/raw/converted-v5/IPv6 \\
    TAG=opus-agent \\
    python3 v8-groundedness.py

Output:
    results/<tag>-v8-groundedness.tsv  (pro Page: Sätze, Scores)
    results/<tag>-v8-groundedness-detail.tsv  (pro Satz: Ergebnis)
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from v8_lib import (
    load_pages, load_sources,
    extract_sentences, chunk_text, TFIDFIndex,
    nli_predict, KIRON_URL, kiron_available,
)

VAULT = Path(os.environ.get("VAULT", "/opt/llm-wiki-bench-opus-agent"))
SOURCES_DIR = Path(os.environ.get("SOURCES", "/opt/llm-wiki/raw/converted-v5/IPv6"))
TAG = os.environ.get("TAG", "v8-test")
RESULTS_DIR = Path(os.environ.get("RESULTS", "/opt/llm-wiki/benchmark/results"))

THRESHOLD_GROUNDED = float(os.environ.get("THRESHOLD_GROUNDED", "0.5"))
THRESHOLD_CONTRADICTION = float(os.environ.get("THRESHOLD_CONTRADICTION", "0.8"))
TOP_K_CHUNKS = int(os.environ.get("TOP_K_CHUNKS", "5"))


def main():
    print(f"v8 Groundedness-Check", flush=True)
    print(f"  Vault:   {VAULT}", flush=True)
    print(f"  Sources: {SOURCES_DIR}", flush=True)
    print(f"  Tag:     {TAG}", flush=True)
    print(flush=True)

    # KIron API check
    print(f"  KIron API: {KIRON_URL}", flush=True)
    if kiron_available():
        print(f"  ✓ KIron erreichbar (GPU-Inferenz)", flush=True)
    else:
        print(f"  ⚠ KIron nicht erreichbar — Fallback auf lokales Modell (CPU, langsam)", flush=True)

    # Load sources and build TF-IDF index
    sources = load_sources(SOURCES_DIR)
    all_chunks = []  # (source_name, chunk_text)
    for name, content in sources.items():
        for chunk in chunk_text(content, chunk_size=400, overlap=80):
            all_chunks.append((name, chunk))

    chunk_texts = [c[1] for c in all_chunks]
    print(f"  Quellen: {len(sources)}, Chunks: {len(all_chunks)}", flush=True)

    print("  Baue TF-IDF-Index...", flush=True)
    tfidf = TFIDFIndex(chunk_texts)
    print(f"  ✓ Index: {len(tfidf.vocab)} Terme", flush=True)

    # Load pages and extract all sentences
    pages = load_pages(VAULT)
    print(f"  Pages: {len(pages)}", flush=True)

    # Phase 1: Extract sentences and find best chunks per sentence
    print("  Extrahiere Sätze und suche Chunks...", flush=True)
    page_data = []  # list of (rel_path, sentences)
    global_pairs = []  # all (chunk_text, sentence) pairs for NLI
    global_map = []    # (page_idx, sent_idx, chunk_idx, source_name) per pair

    for rel_path, content in sorted(pages.items()):
        sents = extract_sentences(content)
        page_idx = len(page_data)
        page_data.append((rel_path, sents))

        for si, sent in enumerate(sents):
            results = tfidf.query(sent, top_k=TOP_K_CHUNKS)
            for ci, (chunk_idx, _score) in enumerate(results):
                global_pairs.append((chunk_texts[chunk_idx], sent))
                global_map.append((page_idx, si, ci, all_chunks[chunk_idx][0]))

    total_sents = sum(len(s) for _, s in page_data)
    print(f"  Sätze: {total_sents}, NLI-Paare: {len(global_pairs)}", flush=True)

    # Phase 2: Global NLI batch
    print(f"  NLI-Batch ({len(global_pairs)} Paare)...", flush=True)
    t0 = time.time()
    nli_results = nli_predict(global_pairs, batch_size=256) if global_pairs else []
    nli_dt = time.time() - t0
    print(f"  ✓ NLI fertig ({nli_dt:.1f}s, {len(global_pairs)/max(nli_dt,0.1):.0f} Paare/s)", flush=True)

    # Phase 3: Aggregate and write results
    print("  Aggregiere Ergebnisse...", flush=True)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    page_tsv = RESULTS_DIR / f"{TAG}-v8-groundedness.tsv"
    detail_tsv = RESULTS_DIR / f"{TAG}-v8-groundedness-detail.tsv"

    # Build per-sentence best scores
    # sent_key = (page_idx, sent_idx) → {entailment, contradiction, source, passage}
    sent_best = {}
    for pair_idx, ((pi, si, ci, src_name), res) in enumerate(zip(global_map, nli_results)):
        key = (pi, si)
        ent = res["scores"]["entailment"]
        con = res["scores"]["contradiction"]

        if key not in sent_best:
            sent_best[key] = {"entailment": 0.0, "contradiction": 0.0,
                              "source": "", "passage": ""}

        if ent > sent_best[key]["entailment"]:
            sent_best[key]["entailment"] = ent
            sent_best[key]["source"] = src_name
            pair_chunk = global_pairs[pair_idx][0]
            sent_best[key]["passage"] = pair_chunk[:120].replace("\t", " ").replace("\n", " ")

        if con > sent_best[key]["contradiction"]:
            sent_best[key]["contradiction"] = con

    total_grounded = 0
    total_ungrounded = 0
    total_contradictions = 0

    with open(page_tsv, "w") as fp, open(detail_tsv, "w") as fd:
        fp.write("page\tsentences\tgrounded\tungrounded\tcontradicted\tscore\n")
        fd.write("page\tsentence\tlabel\tentailment\tbest_source\tbest_passage\n")

        for pi, (rel_path, sents) in enumerate(page_data):
            if not sents:
                fp.write(f"{rel_path}\t0\t0\t0\t0\t1.0\n")
                continue

            grounded = 0
            ungrounded = 0
            contradicted = 0

            for si, sent in enumerate(sents):
                key = (pi, si)
                best = sent_best.get(key, {"entailment": 0.0, "contradiction": 0.0,
                                            "source": "", "passage": ""})

                if best["entailment"] >= THRESHOLD_GROUNDED:
                    label = "entailment"
                    grounded += 1
                elif best["contradiction"] >= THRESHOLD_CONTRADICTION:
                    label = "contradiction"
                    contradicted += 1
                else:
                    label = "neutral"
                    ungrounded += 1

                sent_clean = sent[:200].replace("\t", " ").replace("\n", " ")
                fd.write(f"{rel_path}\t{sent_clean}\t{label}\t{best['entailment']:.3f}\t{best['source']}\t{best['passage']}\n")

            score = grounded / len(sents)
            total_grounded += grounded
            total_ungrounded += ungrounded
            total_contradictions += contradicted

            status = "✓" if score >= 0.7 else "⚠" if score >= 0.4 else "✗"
            fp.write(f"{rel_path}\t{len(sents)}\t{grounded}\t{ungrounded}\t{contradicted}\t{score:.3f}\n")
            print(f"  [{pi+1}/{len(page_data)}] {status} {rel_path}: {grounded}/{len(sents)} grounded ({score:.0%})", flush=True)

    # Summary
    overall = total_grounded / total_sents if total_sents else 1.0
    print(flush=True)
    print(f"  {'='*50}", flush=True)
    print(f"  Groundedness-Score (gesamt): {overall:.1%}", flush=True)
    print(f"  Sätze: {total_sents} total, {total_grounded} grounded, {total_ungrounded} neutral, {total_contradictions} contradicted", flush=True)
    print(f"  NLI-Throughput: {len(global_pairs)/max(nli_dt,0.1):.0f} Paare/s", flush=True)
    print(f"  Reports:", flush=True)
    print(f"    {page_tsv}", flush=True)
    print(f"    {detail_tsv}", flush=True)


if __name__ == "__main__":
    main()

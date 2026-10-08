#!/usr/bin/env bash
# codex-benchmark.sh — Vollständiger IPv6-Benchmark mit Codex/GPT 5.4
#
# Läuft alle 7 IPv6-Quellen sequenziell durch codex-ingest.sh.
# Ergebnisse in benchmark/results/gpt54-*.tsv
#
# Usage:
#   ./codex-benchmark.sh
#   VAULT=/opt/llm-wiki-bench-gpt54 MODEL=gpt-5.4 ./codex-benchmark.sh
#
set -uo pipefail

BENCH_DIR="$(cd "$(dirname "$0")" && pwd)"
VAULT="${VAULT:-/opt/llm-wiki-bench-gpt54}"
MODEL="${MODEL:-gpt-5.4}"
CONVERTED="/opt/llm-wiki/raw/converted"
STATS="$BENCH_DIR/results/gpt54-v5-ingest.tsv"
METRICS="$BENCH_DIR/results/gpt54-v5-metrics.tsv"

mkdir -p "$BENCH_DIR/results"

echo "╔══════════════════════════════════════════╗"
echo "║  CODEX BENCHMARK: $MODEL                ║"
echo "║  Start: $(date)                          ║"
echo "╚══════════════════════════════════════════╝"

# ── Vault vorbereiten ──
mkdir -p "$VAULT"/{wiki/{topics,entities,concepts,tools,procedures,summaries},raw,benchmark-results}
cd "$VAULT/raw"
ln -sfn /wiki_raw_data/knowladgebase kb 2>/dev/null
ln -sfn /opt/llm-wiki/raw/converted converted 2>/dev/null
[[ ! -f "$VAULT/CLAUDE.md" ]] && cp /opt/llm-wiki/CLAUDE.md "$VAULT/"

# Wiki leeren für frischen Benchmark
rm -rf "$VAULT"/wiki/{topics,entities,concepts,tools,procedures,summaries}/* 2>/dev/null
mkdir -p "$VAULT"/wiki/{topics,entities,concepts,tools,procedures,summaries}

printf "source\tbytes\tduration_s\tpages_before\tpages_after\tnew_pages\twl_total\tstatus\n" > "$STATS"

SOURCES=(
  "raw/converted/IPv6/IPv6.md"
  "raw/converted/IPv6/IPv6 Adresszusammensetzung.md"
  "raw/converted/IPv6/IPv6 Multicast-Adressen.md"
  "raw/converted/IPv6/IPv6 Routing.md"
  "raw/converted/IPv6/IPv6 Firewall-Konfiguration.md"
  "raw/converted/IPv6/ipv6_cheat_sheet.md"
  "raw/converted/IPv6/ISi-L-IPv6 - Leitfaden für eine sichere IPv6-Netzwerkarchitektur.md"
)

for src in "${SOURCES[@]}"; do
  bytes=$(wc -c < "$VAULT/$src" 2>/dev/null || echo 0)
  pages_before=$(find "$VAULT/wiki" -name "*.md" | wc -l)

  echo ""
  echo "[$(date +%H:%M:%S)] === $(basename "$src") ($bytes B) ==="

  T_START=$(date +%s)
  VAULT="$VAULT" MODEL="$MODEL" "$BENCH_DIR/codex-ingest.sh" "$src" 2>&1 || true
  T_END=$(date +%s)
  duration=$((T_END - T_START))

  pages_after=$(find "$VAULT/wiki" -name "*.md" | wc -l)
  new_pages=$((pages_after - pages_before))
  wl_total=$(find "$VAULT/wiki" -name "*.md" -exec grep -oh '\[\[' {} + 2>/dev/null | wc -l)

  status="ok"
  [[ $new_pages -eq 0 ]] && status="no_pages"

  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
    "$(basename "$src")" "$bytes" "$duration" "$pages_before" "$pages_after" \
    "$new_pages" "$wl_total" "$status" >> "$STATS"
done

# ── Metriken sammeln ──
{
  total_pages=$(find "$VAULT/wiki" -name "*.md" | wc -l)
  total_wl=$(find "$VAULT/wiki" -name "*.md" -exec grep -oh '\[\[' {} + 2>/dev/null | wc -l)
  stubs=0
  for f in $(find "$VAULT/wiki" -name "*.md"); do
    body=$(awk '/^---$/{n++}n>=2' "$f" | tail -n +2)
    sentences=$(echo "$body" | grep -oP '[.!?]' | wc -l)
    [[ $sentences -lt 3 ]] && stubs=$((stubs+1))
  done
  selfrefs=0
  for f in $(find "$VAULT/wiki" -name "*.md"); do
    name=$(basename "$f" .md)
    grep -q "\[\[$name\]\]" "$f" 2>/dev/null && selfrefs=$((selfrefs+1))
  done
  underscore_pages=$(find "$VAULT/wiki" -name "*_*" | wc -l)
  printf "metric\tvalue\n"
  printf "model\t%s\n" "$MODEL"
  printf "total_pages\t%s\n" "$total_pages"
  printf "total_wikilinks\t%s\n" "$total_wl"
  printf "wl_per_page\t%.1f\n" "$(echo "scale=1; $total_wl / ($total_pages + 1)" | bc)"
  printf "stubs\t%s\n" "$stubs"
  printf "self_references\t%s\n" "$selfrefs"
  printf "underscore_names\t%s\n" "$underscore_pages"
  for d in topics summaries entities concepts tools procedures; do
    printf "%s\t%s\n" "$d" "$(ls "$VAULT/wiki/$d/" 2>/dev/null | wc -l)"
  done
} > "$METRICS"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  CODEX BENCHMARK FERTIG                  ║"
echo "║  Ende: $(date)                           ║"
echo "╚══════════════════════════════════════════╝"
echo ""
column -t -s$'\t' "$METRICS"
echo ""
echo "Ingest-Stats: $STATS"
echo "Metriken:     $METRICS"
echo "Vault:        $VAULT"

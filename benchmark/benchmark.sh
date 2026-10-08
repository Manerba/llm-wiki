#!/usr/bin/env bash
# benchmark.sh — Reproduzierbarer Ingest-Benchmark für LLM-Wiki
#
# Usage:
#   ./benchmark.sh <model-tag> <env-file> <schema-mode> [vault-path]
#
# Beispiel:
#   ./benchmark.sh phi4-14b configs/phi4-14b.env free /opt/llm-wiki-phi4
#   ./benchmark.sh qwen3-8b configs/qwen3-8b.env strict /opt/llm-wiki-qwen
#   ./benchmark.sh qwen35 configs/qwen35.env free /opt/llm-wiki-qwen35
#
# Was es tut:
#   1. Vault erstellen/resetten (wiki/ leeren, log.md+index.md schreiben)
#   2. Alle 7 IPv6-Quellen mit v4.py ingesten
#   3. Automatische Metriken sammeln (Pages, WL, Stubs, Self-Refs, Naming, Duplikate)
#   4. Ergebnis nach results/<model-tag>-v4.tsv schreiben

set -uo pipefail

TAG="${1:?Usage: benchmark.sh <tag> <env> <schema> [vault]}"
BENCH_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$(cd "$BENCH_DIR" && realpath "$2")"
SCHEMA_MODE="${3:?}"
VAULT="${4:-/opt/llm-wiki-bench-$TAG}"
SCRIPT="$BENCH_DIR/versions/v4.py"
RESULTS_DIR="$BENCH_DIR/results"
CONVERTED="/opt/llm-wiki/raw/converted"

mkdir -p "$RESULTS_DIR"

echo "═══════════════════════════════════════════"
echo "  BENCHMARK: $TAG (v4, $SCHEMA_MODE)"
echo "  Vault: $VAULT"
echo "  Env: $ENV_FILE"
echo "═══════════════════════════════════════════"

# ── 1. Vault erstellen/resetten ──
mkdir -p "$VAULT"/{wiki/{topics,entities,concepts,tools,procedures,summaries},raw,docs,scripts}
cd "$VAULT/raw" && ln -sfn /wiki_raw_data/knowladgebase kb 2>/dev/null; ln -sfn /opt/llm-wiki/raw/converted converted 2>/dev/null
rm -rf "$VAULT"/wiki/{topics,entities,concepts,tools,procedures,summaries}/* 2>/dev/null
cat > "$VAULT/log.md" <<EOF
# Wiki-Log ($TAG v4 benchmark)
## [$(date +%Y-%m-%d\ %H:%M)] init | benchmark start
EOF
# Schema kopieren falls nicht vorhanden
[[ ! -f "$VAULT/CLAUDE.md" ]] && cp /opt/llm-wiki/CLAUDE.md "$VAULT/"

echo "[$(date +%H:%M:%S)] Vault bereit."

# ── 2. Ingest ──
STATS="$RESULTS_DIR/${TAG}-v4-ingest.tsv"
printf "source\tbytes\tduration_s\tprompt_tok\tcompletion_tok\tpages_planned\tpages_written\twl_added\tself_refs\ttruncated\tstatus\n" > "$STATS"

SOURCES=(
  "$CONVERTED/IPv6/IPv6.md"
  "$CONVERTED/IPv6/IPv6 Adresszusammensetzung.md"
  "$CONVERTED/IPv6/IPv6 Multicast-Adressen.md"
  "$CONVERTED/IPv6/IPv6 Routing.md"
  "$CONVERTED/IPv6/IPv6 Firewall-Konfiguration.md"
  "$CONVERTED/IPv6/ipv6_cheat_sheet.md"
  "$CONVERTED/IPv6/ISi-L-IPv6 - Leitfaden für eine sichere IPv6-Netzwerkarchitektur.md"
)

for src in "${SOURCES[@]}"; do
  bytes=$(wc -c < "$src" 2>/dev/null || echo 0)
  echo ""
  echo "[$(date +%H:%M:%S)] === $(basename "$src") ($bytes B) ==="

  out=$(VAULT="$VAULT" ENV_FILE="$ENV_FILE" SCHEMA_MODE="$SCHEMA_MODE" python3 "$SCRIPT" "$src" 2>&1) || true
  echo "$out"

  duration=$(echo "$out" | grep -oP 'Total:\s*\K[0-9.]+' | head -1)
  prompt=$(echo "$out" | grep -oP 'prompt=\K[0-9]+' | tail -1)
  completion=$(echo "$out" | grep -oP 'completion=\K[0-9]+' | tail -1)
  planned=$(echo "$out" | grep -oP '(\d+) Pages geplant' | grep -oP '\d+' | head -1)
  written=$(echo "$out" | grep -oP 'Pages geschrieben:\s*\K[0-9]+' | head -1)
  wl=$(echo "$out" | grep -oP 'augmentiert:\s*\K[0-9]+' | head -1)
  selfref=$(echo "$out" | grep -oP 'Self-Refs entfernt:\s*\K[0-9]+' | head -1)
  trunc=$(echo "$out" | grep -oP 'Truncated/skipped:\s*\K[0-9]+' | head -1)

  status="ok"
  echo "$out" | grep -q "⚠\|Traceback\|Error" && status="warn"
  echo "$out" | grep -q "Traceback\|sys.exit" && status="fail"

  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
    "$(basename "$src")" "$bytes" "${duration:-0}" "${prompt:-0}" "${completion:-0}" \
    "${planned:-0}" "${written:-0}" "${wl:-0}" "${selfref:-0}" "${trunc:-0}" "$status" >> "$STATS"
done

# ── 3. Automatische Metriken ──
METRICS="$RESULTS_DIR/${TAG}-v4-metrics.tsv"
{
  total_pages=$(find "$VAULT/wiki" -name "*.md" | grep -v wiki-v | wc -l)
  total_wl=$(find "$VAULT/wiki" -name "*.md" | grep -v wiki-v | xargs grep -oh '\[\[' 2>/dev/null | wc -l)

  # Stubs (< 3 Sätze Body nach dem Frontmatter)
  stubs=0
  for f in $(find "$VAULT/wiki" -name "*.md" | grep -v wiki-v); do
    body=$(awk '/^---$/{n++}n>=2' "$f" | tail -n +2)
    sentences=$(echo "$body" | grep -oP '[.!?]' | wc -l)
    [[ $sentences -lt 3 ]] && stubs=$((stubs+1))
  done

  # Self-References
  selfrefs=0
  for f in $(find "$VAULT/wiki" -name "*.md" | grep -v wiki-v); do
    name=$(basename "$f" .md)
    if grep -q "\[\[$name\]\]" "$f" 2>/dev/null; then
      selfrefs=$((selfrefs+1))
    fi
  done

  # Naming: Underscores
  underscore_pages=$(find "$VAULT/wiki" -name "*_*" | grep -v wiki-v | wc -l)

  # Per-type counts
  for d in topics summaries entities concepts tools procedures; do
    eval "${d}_count=$(ls "$VAULT/wiki/$d/" 2>/dev/null | wc -l)"
  done

  printf "metric\tvalue\n"
  printf "total_pages\t%s\n" "$total_pages"
  printf "total_wikilinks\t%s\n" "$total_wl"
  printf "wl_per_page\t%.1f\n" "$(echo "scale=1; $total_wl / ($total_pages + 1)" | bc)"
  printf "stubs\t%s\n" "$stubs"
  printf "stub_rate\t%.1f%%\n" "$(echo "scale=1; $stubs * 100 / ($total_pages + 1)" | bc)"
  printf "self_references\t%s\n" "$selfrefs"
  printf "underscore_names\t%s\n" "$underscore_pages"
  printf "topics\t%s\n" "$topics_count"
  printf "summaries\t%s\n" "$summaries_count"
  printf "entities\t%s\n" "$entities_count"
  printf "concepts\t%s\n" "$concepts_count"
  printf "tools\t%s\n" "$tools_count"
  printf "procedures\t%s\n" "$procedures_count"
} > "$METRICS"

echo ""
echo "═══════════════════════════════════════════"
echo "  ERGEBNIS: $TAG v4"
echo "═══════════════════════════════════════════"
column -t -s$'\t' "$METRICS"
echo ""
echo "Ingest-Stats: $STATS"
echo "Metriken: $METRICS"
echo "Vault: $VAULT"

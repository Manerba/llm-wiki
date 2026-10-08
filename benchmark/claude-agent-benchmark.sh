#!/usr/bin/env bash
# claude-agent-benchmark.sh — Benchmark im AGENT-Modus (nicht One-Shot)
#
# Unterschied zu claude-benchmark.sh:
#   claude-benchmark.sh: `claude -p` One-Shot → Text raus → Parser
#   claude-agent-benchmark.sh: `claude -p --allowedTools --max-turns` → Agent liest/schreibt Dateien
#
# Das ist fair vergleichbar mit codex exec --full-auto (GPT 5.4 Agent-Modus).
#
# Usage:
#   ./claude-agent-benchmark.sh opus claude-opus-4-6
#   ./claude-agent-benchmark.sh sonnet claude-sonnet-4-6
#   ./claude-agent-benchmark.sh haiku claude-haiku-4-5-20251001
#
set -uo pipefail

TAG="${1:?Usage: claude-agent-benchmark.sh <tag> <model-id>}"
MODEL="${2:?}"
BENCH_DIR="$(cd "$(dirname "$0")" && pwd)"
VAULT="/opt/llm-wiki-bench-${TAG}"
CONVERTED="/opt/llm-wiki/raw/converted-v5"
RESULTS="$BENCH_DIR/results"
STATS="$RESULTS/${TAG}-v5-ingest.tsv"

mkdir -p "$RESULTS"

echo "╔══════════════════════════════════════════╗"
echo "║  CLAUDE AGENT BENCHMARK: $MODEL          ║"
echo "║  Tag: $TAG                               ║"
echo "║  Modus: Agent (--allowedTools, max-turns)║"
echo "║  Start: $(date)                          ║"
echo "╚══════════════════════════════════════════╝"

# Vault vorbereiten
mkdir -p "$VAULT"/{wiki/{topics,entities,concepts,tools,procedures,summaries},raw}
cd "$VAULT/raw" && ln -sfn /wiki_raw_data/knowladgebase kb 2>/dev/null
ln -sfn /opt/llm-wiki/raw/converted-v5 converted 2>/dev/null
rm -rf "$VAULT"/wiki/{topics,entities,concepts,tools,procedures,summaries}/* 2>/dev/null
cp /opt/llm-wiki/CLAUDE.md "$VAULT/" 2>/dev/null

printf "source\tbytes\tduration_s\tpages_before\tpages_after\tnew_pages\twl_total\tstatus\n" > "$STATS"

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
  [[ ! -f "$src" ]] && continue
  bytes=$(wc -c < "$src")
  src_name=$(basename "$src")
  pages_before=$(find "$VAULT/wiki" -name "*.md" | wc -l)

  echo ""
  echo "[$(date +%H:%M:%S)] === $src_name ($bytes B) ==="

  # Quelldatei ins Vault kopieren damit der Agent sie lesen kann
  mkdir -p "$VAULT/raw/current"
  cp "$src" "$VAULT/raw/current/"

  T_START=$(date +%s)

  # Agent-Prompt: Claude bekommt Dateizugriff und arbeitet wie Codex
  PROMPT="Du arbeitest im Verzeichnis $VAULT.

Lies zuerst CLAUDE.md (das Schema). Dann lies die Quelle raw/current/$src_name.

Führe einen vollständigen Ingest dieser Quelle durch:
1. Erstelle eine Summary-Page in wiki/summaries/
2. Leite Entities, Concepts, Tools und Procedures ab
3. Schreibe jede Page als eigene Datei (mit Frontmatter)
4. Setze Wikilinks zu existierenden Pages
5. Schreibe auf Deutsch, Dateinamen mit Spaces

Schreibe die Pages direkt als Dateien. Kein ===PAGE:=== Format nötig."

  # Claude im Agent-Modus: Prompt via stdin (nicht als Argument!)
  printf '%s' "$PROMPT" | claude -p \
    --model "$MODEL" \
    --max-turns 50 \
    --allowedTools "Read,Write,Edit,Bash,Glob,Grep" \
    --add-dir "$VAULT" 2>/dev/null || true

  T_END=$(date +%s)
  duration=$((T_END - T_START))

  pages_after=$(find "$VAULT/wiki" -name "*.md" | wc -l)
  new_pages=$((pages_after - pages_before))
  wl_total=$(find "$VAULT/wiki" -name "*.md" -print0 | xargs -0 grep -oh '\[\[' 2>/dev/null | wc -l)

  status="ok"
  [[ $new_pages -eq 0 ]] && status="no_pages"

  echo "  → $new_pages neue Pages, $wl_total WL total, ${duration}s"

  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
    "$src_name" "$bytes" "$duration" "$pages_before" "$pages_after" \
    "$new_pages" "$wl_total" "$status" >> "$STATS"

  # Aufräumen
  rm -rf "$VAULT/raw/current"
done

# Metriken
METRICS="$RESULTS/${TAG}-v5-metrics.tsv"
total_pages=$(find "$VAULT/wiki" -name "*.md" | wc -l)
total_wl=$(find "$VAULT/wiki" -name "*.md" -print0 | xargs -0 grep -oh '\[\[' 2>/dev/null | wc -l)
selfref=$(find "$VAULT/wiki" -name "*.md" -print0 | xargs -0 -I{} sh -c 'n=$(basename "{}" .md); grep -l "\[\[$n\]\]" "{}" 2>/dev/null' | wc -l)
{
  printf "metric\tvalue\n"
  printf "model\t%s\n" "$MODEL"
  printf "mode\tagent\n"
  printf "total_pages\t%s\n" "$total_pages"
  printf "total_wikilinks\t%s\n" "$total_wl"
  printf "self_references\t%s\n" "$selfref"
  for d in topics summaries entities concepts tools procedures; do
    printf "%s\t%s\n" "$d" "$(ls "$VAULT/wiki/$d/" 2>/dev/null | wc -l)"
  done
} > "$METRICS"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  FERTIG: $TAG ($MODEL) AGENT-MODUS       ║"
echo "╚══════════════════════════════════════════╝"
column -t -s$'\t' "$METRICS"

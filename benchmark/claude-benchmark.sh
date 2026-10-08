#!/usr/bin/env bash
# claude-benchmark.sh — Benchmark für Claude-Modelle via `claude -p`
#
# Usage:
#   ./claude-benchmark.sh sonnet claude-sonnet-4-6
#   ./claude-benchmark.sh haiku claude-haiku-4-5-20251001
#   ./claude-benchmark.sh opus claude-opus-4-6
#
set -uo pipefail

TAG="${1:?Usage: claude-benchmark.sh <tag> <model-id>}"
MODEL="${2:?}"
BENCH_DIR="$(cd "$(dirname "$0")" && pwd)"
VAULT="/opt/llm-wiki-bench-${TAG}"
CONVERTED="/opt/llm-wiki/raw/converted-v5"
RESULTS="$BENCH_DIR/results"
STATS="$RESULTS/${TAG}-v5-ingest.tsv"
METRICS="$RESULTS/${TAG}-v5-metrics.tsv"

mkdir -p "$RESULTS"

echo "╔══════════════════════════════════════════╗"
echo "║  CLAUDE BENCHMARK: $MODEL               ║"
echo "║  Tag: $TAG                               ║"
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

# Existierende Page-Namen sammeln (wächst mit jedem Ingest)
get_existing_pages() {
  find "$VAULT/wiki" -name "*.md" -exec basename {} .md \; 2>/dev/null | sort -u | head -80 | sed 's/^/  - [[/' | sed 's/$/]]/'
}

for src in "${SOURCES[@]}"; do
  [[ ! -f "$src" ]] && continue
  bytes=$(wc -c < "$src")
  src_name=$(basename "$src")
  pages_before=$(find "$VAULT/wiki" -name "*.md" | wc -l)
  existing=$(get_existing_pages)

  echo ""
  echo "[$(date +%H:%M:%S)] === $src_name ($bytes B) ==="

  T_START=$(date +%s)

  # Claude bekommt: Schema-Kontext + Quelle + existierende Pages + Anweisung
  PROMPT="Du arbeitest im Wiki-Vault unter $VAULT.

Lies das Schema in CLAUDE.md (Karpathys LLM-Wiki-Pattern) und ingestiere die folgende Quelle.

WICHTIG — Output-Format:
Schreibe JEDE Wiki-Page als separaten Block, beginnend mit:
===PAGE: wiki/<typ>/<Name>.md===
Direkt darunter der vollständige Inhalt (Frontmatter + Body).
Kein Vor-/Nachwort.

Regeln:
- Deutsch, Klartext mit Spaces in Dateinamen
- Frontmatter: type, aliases, tags, created, updated, sources (Block-Liste mit String-Quotes)
- Wikilinks [[Page]] aktiv im Body setzen
- Halluziniere nicht — nur was in der Quelle steht
- Tabelleneinträge sind KEINE eigenen Pages

Existierende Pages (für Wikilinks):
$existing

Quelle ($src_name):
$(head -c 15000 "$src")

Erzeuge jetzt die Wiki-Pages im ===PAGE:...=== Format."

  # Prompt in temporäre Datei schreiben (Shell-Variable-Limits umgehen)
  PROMPT_FILE=$(mktemp)
  printf '%s' "$PROMPT" > "$PROMPT_FILE"

  # Claude CLI non-interaktiv aufrufen
  RESPONSE=$(claude -p --model "$MODEL"  < "$PROMPT_FILE" 2>/dev/null) || true
  rm -f "$PROMPT_FILE"

  T_END=$(date +%s)
  duration=$((T_END - T_START))

  # Pages extrahieren und schreiben
  echo "$RESPONSE" | python3 "$BENCH_DIR/parse-pages.py" "$VAULT"

  pages_after=$(find "$VAULT/wiki" -name "*.md" | wc -l)
  new_pages=$((pages_after - pages_before))
  wl_total=$(find "$VAULT/wiki" -name "*.md" -print0 | xargs -0 grep -oh '\[\[' 2>/dev/null | wc -l)

  status="ok"
  [[ $new_pages -eq 0 ]] && status="no_pages"

  echo "  → $new_pages neue Pages, $wl_total WL total, ${duration}s"

  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
    "$src_name" "$bytes" "$duration" "$pages_before" "$pages_after" \
    "$new_pages" "$wl_total" "$status" >> "$STATS"
done

# Metriken
total_pages=$(find "$VAULT/wiki" -name "*.md" | wc -l)
total_wl=$(find "$VAULT/wiki" -name "*.md" -print0 | xargs -0 grep -oh '\[\[' 2>/dev/null | wc -l)
selfref=$(find "$VAULT/wiki" -name "*.md" -print0 | xargs -0 -I{} sh -c 'n=$(basename "{}" .md); grep -l "\[\[$n\]\]" "{}" 2>/dev/null' | wc -l)
{
  printf "metric\tvalue\n"
  printf "model\t%s\n" "$MODEL"
  printf "total_pages\t%s\n" "$total_pages"
  printf "total_wikilinks\t%s\n" "$total_wl"
  printf "self_references\t%s\n" "$selfref"
  for d in topics summaries entities concepts tools procedures; do
    printf "%s\t%s\n" "$d" "$(ls "$VAULT/wiki/$d/" 2>/dev/null | wc -l)"
  done
} > "$METRICS"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  FERTIG: $TAG ($MODEL)                   ║"
echo "╚══════════════════════════════════════════╝"
column -t -s$'\t' "$METRICS"

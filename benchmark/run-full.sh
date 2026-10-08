#!/usr/bin/env bash
# run-full.sh — Master-Runner: Kompletter Benchmark für ein Modell in einem Befehl
#
# Usage:
#   # Claude-Familie (Opus, Sonnet, Haiku):
#   ./run-full.sh opus claude-opus-4-6 claude
#
#   # GPT via Codex:
#   ./run-full.sh gpt54 gpt-5.4 codex
#
#   # Lokales Modell (Ollama):
#   ./run-full.sh qwen35 configs/qwen35.env ollama free
#   ./run-full.sh qwen3-8b configs/qwen3-8b.env ollama strict
#
# Was es tut (sequenziell):
#   1. Ingest     — alle 7 IPv6-Quellen verarbeiten
#   2. Metriken   — Pages, WL, Stubs, Self-Refs, Naming zählen
#   3. Fehler     — jede Page auf 6 Fehlertypen prüfen (via claude -p)
#   4. LWQS       — Composite-Score berechnen
#   5. Self-Review — Modell reviewt eigene Pages
#   6. Report     — Zusammenfassung als Markdown
#
set -uo pipefail

TAG="${1:?Usage: run-full.sh <tag> <model-id-or-env> <type: claude|codex|ollama> [schema: strict|free]}"
MODEL="${2:?}"
TYPE="${3:?Type: claude, codex, oder ollama}"
SCHEMA="${4:-free}"

BENCH_DIR="$(cd "$(dirname "$0")" && pwd)"
VAULT="/opt/llm-wiki-bench-${TAG}"
RESULTS="$BENCH_DIR/results"
CONVERTED="/opt/llm-wiki/raw/converted-v5"
REPORT="$RESULTS/${TAG}-full-report.md"
TIMESTAMP=$(date +%Y-%m-%d_%H%M)

mkdir -p "$RESULTS"

echo "╔══════════════════════════════════════════════════════╗"
echo "║  FULL BENCHMARK: $TAG                               ║"
echo "║  Modell: $MODEL                                     ║"
echo "║  Typ: $TYPE | Schema: $SCHEMA                       ║"
echo "║  Start: $(date)                                     ║"
echo "╚══════════════════════════════════════════════════════╝"

# ═══════════════════════════════════════════════════════════
# SCHRITT 1: INGEST
# ═══════════════════════════════════════════════════════════
echo ""
echo "━━━ Schritt 1/6: INGEST ━━━"

case "$TYPE" in
  claude)
    "$BENCH_DIR/claude-benchmark.sh" "$TAG" "$MODEL"
    ;;
  claude-agent)
    "$BENCH_DIR/claude-agent-benchmark.sh" "$TAG" "$MODEL"
    ;;
  codex)
    VAULT="$VAULT" MODEL="$MODEL" "$BENCH_DIR/codex-benchmark.sh"
    ;;
  ollama)
    "$BENCH_DIR/benchmark.sh" "$TAG" "$MODEL" "$SCHEMA" "$VAULT"
    ;;
  *)
    echo "ERROR: Unbekannter Typ '$TYPE'. Erlaubt: claude, claude-agent, codex, ollama" >&2
    exit 1
    ;;
esac

TOTAL_PAGES=$(find "$VAULT/wiki" -name "*.md" 2>/dev/null | wc -l)
if [[ $TOTAL_PAGES -eq 0 ]]; then
  echo "ERROR: Ingest hat 0 Pages erzeugt. Abbruch." >&2
  exit 1
fi
echo "✓ Ingest abgeschlossen: $TOTAL_PAGES Pages"

# ═══════════════════════════════════════════════════════════
# SCHRITT 2: METRIKEN
# ═══════════════════════════════════════════════════════════
echo ""
echo "━━━ Schritt 2/6: METRIKEN ━━━"

TOTAL_WL=$(find "$VAULT/wiki" -name "*.md" -print0 2>/dev/null | xargs -0 grep -oh '\[\[' 2>/dev/null | wc -l)
WL_PER_PAGE=$(echo "scale=1; $TOTAL_WL / ($TOTAL_PAGES)" | bc 2>/dev/null || echo "0")
SELF_REFS=$(find "$VAULT/wiki" -name "*.md" -print0 2>/dev/null | xargs -0 -I{} sh -c 'n=$(basename "{}" .md); grep -l "\[\[$n\]\]" "{}" 2>/dev/null' | wc -l)
SELF_REF_RATE=$(echo "scale=1; $SELF_REFS * 100 / $TOTAL_PAGES" | bc 2>/dev/null || echo "0")
UNDERSCORES=$(find "$VAULT/wiki" -name "*_*" 2>/dev/null | wc -l)

for d in topics summaries entities concepts tools procedures; do
  eval "${d}_count=$(ls "$VAULT/wiki/$d/" 2>/dev/null | wc -l)"
done

echo "  Pages: $TOTAL_PAGES"
echo "  Wikilinks: $TOTAL_WL ($WL_PER_PAGE/Page)"
echo "  Self-Refs: $SELF_REFS ($SELF_REF_RATE%)"
echo "  Underscores: $UNDERSCORES"
echo "  Summaries=$summaries_count Entities=$entities_count Concepts=$concepts_count Tools=$tools_count Procedures=$procedures_count Topics=$topics_count"

# ═══════════════════════════════════════════════════════════
# SCHRITT 3: FEHLERRATEN-ANALYSE
# ═══════════════════════════════════════════════════════════
echo ""
echo "━━━ Schritt 3/6: FEHLERRATEN-ANALYSE ━━━"

SOURCES_DIR="/opt/llm-wiki/raw/converted-v5/IPv6"

# Alle Pages sammeln
PAGE_LIST=$(find "$VAULT/wiki" -name "*.md" | sort)
TOTAL_CHECK=$(echo "$PAGE_LIST" | wc -l)
FEHLER_COUNT=0
OK_COUNT=0
K_COUNT=0; H_COUNT=0; N_COUNT=0; S_COUNT=0; T_COUNT=0; R_COUNT=0

echo "  Prüfe $TOTAL_CHECK Pages..."
echo "  Phase A: Deterministische Checks (K, S, T, R, N) ..."

# ── V8: NLI-Groundedness statt LLM-Halluzinations-Check ──
V8="${V8:-1}"
GROUND_TSV="$RESULTS/${TAG}-v8-groundedness.tsv"
if [[ "$V8" == "1" ]]; then
  echo "  Phase B: NLI-Groundedness-Check (v8, deterministisch) ..."
  VAULT="$VAULT" SOURCES="$SOURCES_DIR" TAG="$TAG" RESULTS="$RESULTS" \
    python3 "$BENCH_DIR/versions/v8-groundedness.py" 2>&1 | grep -E "^  [✓⚠✗=]|Groundedness"
else
  echo "  Phase B: LLM-Halluzinations-Check (H) mit Quellenzugriff ..."
fi

# Header für TSV
FEHLER_TSV="$RESULTS/${TAG}-fehlerraten.tsv"
printf "page\tok\tK\tH\tN\tS\tT\tR\tissues\n" > "$FEHLER_TSV"

CHECK_NUM=0
while IFS= read -r page; do
  [[ -z "$page" ]] && continue
  CHECK_NUM=$((CHECK_NUM + 1))
  rel="${page#$VAULT/}"
  page_name=$(basename "$page" .md)
  content=$(cat "$page")

  ISSUES=""
  PK=0; PH=0; PN=0; PS=0; PT=0; PR=0

  # ── PHASE A: Deterministische Checks (kein LLM nötig) ──

  # S = Stub: weniger als 3 Sätze Body (nach dem Frontmatter)
  BODY=$(echo "$content" | awk '/^---$/{n++}n>=2' | tail -n +2)
  SENTENCES=$(echo "$BODY" | grep -oP '[.!?]' | wc -l)
  if [[ $SENTENCES -lt 3 ]]; then
    PS=1; ISSUES="${ISSUES}Stub (${SENTENCES} Sätze)|"
  fi

  # T = Truncation: Frontmatter nicht geschlossen oder unvollständig
  FM_COUNT=$(echo "$content" | grep -c '^---$')
  if [[ $FM_COUNT -lt 2 ]]; then
    PT=1; ISSUES="${ISSUES}Truncation (Frontmatter nicht geschlossen)|"
  else
    # Prüfe ob type: vorhanden
    if ! echo "$content" | head -30 | grep -q '^type:'; then
      PT=1; ISSUES="${ISSUES}Truncation (type: fehlt im Frontmatter)|"
    fi
  fi

  # R = Self-Reference: [[PageName]] im eigenen Text
  if grep -q "\[\[$page_name\]\]" "$page" 2>/dev/null; then
    PR=1; ISSUES="${ISSUES}Self-Reference ([[${page_name}]])|"
  fi

  # N = Naming: Underscores im Dateinamen
  if echo "$page_name" | grep -q '_'; then
    PN=1; ISSUES="${ISSUES}Naming (Underscore im Dateinamen)|"
  fi

  # K = Klassifikation: bekannte Fehlmuster deterministisch prüfen
  PAGE_DIR=$(basename "$(dirname "$page")")
  PAGE_LOWER=$(echo "$page_name" | tr '[:upper:]' '[:lower:]')
  # RFCs in tools/ oder entities/ → sollte concepts/ sein
  if echo "$PAGE_LOWER" | grep -qP '^rfc[\s_-]*\d' && [[ "$PAGE_DIR" == "tools" || "$PAGE_DIR" == "entities" ]]; then
    PK=1; ISSUES="${ISSUES}Klassifikation (RFC in ${PAGE_DIR}/ statt concepts/)|"
  fi
  # Tunnel/Adresse/Header in entities/ → sollte concepts/ sein
  if echo "$PAGE_LOWER" | grep -qP '(tunnel|adresse|header|scope|extension|discovery|autoconfiguration)$' && [[ "$PAGE_DIR" == "entities" ]]; then
    PK=1; ISSUES="${ISSUES}Klassifikation (technischer Begriff in entities/ statt concepts/)|"
  fi

  # ── PHASE B: Halluzinations-Check ──

  if [[ "$V8" == "1" ]] && [[ -f "$GROUND_TSV" ]]; then
    # V8: Lookup aus NLI-Groundedness-TSV
    GROUND_LINE=$(grep "^${rel}	" "$GROUND_TSV" 2>/dev/null | head -1)
    if [[ -n "$GROUND_LINE" ]]; then
      GSCORE=$(echo "$GROUND_LINE" | cut -f6)
      if (( $(echo "$GSCORE < 0.5" | bc -l 2>/dev/null) )); then
        PH=1; ISSUES="${ISSUES}Halluzination (Groundedness ${GSCORE})|"
      fi
    fi
  else
    # Fallback: LLM-basierter Halluzinations-Check
    QUELLEN_KONTEXT=""
    for qf in "$SOURCES_DIR"/*.md; do
      [[ ! -f "$qf" ]] && continue
      QF_NAME=$(basename "$qf")
      QF_CONTENT=$(head -c 4000 "$qf")
      QUELLEN_KONTEXT="${QUELLEN_KONTEXT}
=== QUELLE: ${QF_NAME} ===
${QF_CONTENT}
"
    done

    PROMPT_FILE=$(mktemp)
    cat > "$PROMPT_FILE" <<HEREDOC
Prüfe ob diese Wiki-Page Halluzinationen enthält — also Aussagen, die NICHT aus den beigefügten Originalquellen ableitbar sind.

Antworte NUR mit JSON:
{"H": true/false, "issues": ["konkrete Aussage die nicht in den Quellen steht"] oder []}

Regeln:
- Prüfe JEDE faktische Aussage in der Page gegen die Originalquellen unten
- Wenn eine Aussage in KEINER der Quellen steht oder ableitbar ist → H=true
- Strukturelle Aussagen (Frontmatter, Wikilinks, Kategorisierung) ignorieren
- Nur INHALTLICHE Aussagen prüfen

WIKI-PAGE (${rel}):
$(head -c 6000 "$page")

ORIGINALQUELLEN:
${QUELLEN_KONTEXT}
HEREDOC

    REVIEW=$(claude -p --model claude-opus-4-6 \
      --max-turns 5 \
      --allowedTools "Read" \
      --add-dir "$SOURCES_DIR" \
      < "$PROMPT_FILE" 2>/dev/null) || REVIEW='{"H":false}'
    rm -f "$PROMPT_FILE"

    H_RESULT=$(echo "$REVIEW" | python3 -c "
import json, sys, re
text = sys.stdin.read().strip()
m = re.search(r'\{[^}]*\}', text, re.DOTALL)
if m:
    try:
        r = json.loads(m.group())
        h = 1 if r.get('H') else 0
        issues = '|'.join(str(i) for i in r.get('issues', []))
        print(f'{h}\t{issues}')
    except: print('0\t')
else: print('0\t')
" 2>/dev/null || echo "0	")

    PH=$(echo "$H_RESULT" | cut -f1)
    H_ISSUES=$(echo "$H_RESULT" | cut -f2)
    [[ -n "$H_ISSUES" ]] && ISSUES="${ISSUES}${H_ISSUES}|"
  fi

  # ── Zusammenfassung pro Page ──

  TOTAL_ERRORS=$((PK + PH + PN + PS + PT + PR))
  if [[ $TOTAL_ERRORS -eq 0 ]]; then
    PAGE_OK="True"
    OK_COUNT=$((OK_COUNT + 1))
  else
    PAGE_OK="False"
    FEHLER_COUNT=$((FEHLER_COUNT + 1))
  fi

  K_COUNT=$((K_COUNT + PK)); H_COUNT=$((H_COUNT + PH)); N_COUNT=$((N_COUNT + PN))
  S_COUNT=$((S_COUNT + PS)); T_COUNT=$((T_COUNT + PT)); R_COUNT=$((R_COUNT + PR))

  # Issues bereinigen (trailing | entfernen)
  ISSUES_CLEAN=$(echo "$ISSUES" | sed 's/|$//')
  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" "$rel" "$PAGE_OK" "$PK" "$PH" "$PN" "$PS" "$PT" "$PR" "$ISSUES_CLEAN" >> "$FEHLER_TSV"

  STATUS_CHAR="✓"
  [[ "$PAGE_OK" != "True" ]] && STATUS_CHAR="✗"
  printf "\r  [%d/%d] %s %s          " "$CHECK_NUM" "$TOTAL_CHECK" "$STATUS_CHAR" "$rel"
done <<< "$PAGE_LIST"

FEHLERRATE=$(echo "scale=1; $FEHLER_COUNT * 100 / $TOTAL_CHECK" | bc 2>/dev/null || echo "0")
H_RATE=$(echo "scale=1; $H_COUNT * 100 / $TOTAL_CHECK" | bc 2>/dev/null || echo "0")

echo ""
echo "  ✓ Fehleranalyse: $OK_COUNT/$TOTAL_CHECK OK ($FEHLERRATE% Fehlerrate)"
echo "    K=$K_COUNT H=$H_COUNT N=$N_COUNT S=$S_COUNT T=$T_COUNT R=$R_COUNT"

# ═══════════════════════════════════════════════════════════
# SCHRITT 3b: V8 ZITATIONEN + WIDERSPRÜCHE (optional)
# ═══════════════════════════════════════════════════════════
if [[ "$V8" == "1" ]]; then
  echo ""
  echo "━━━ Schritt 3b: V8 ZITATIONEN + WIDERSPRÜCHE ━━━"

  echo "  Zitationsextraktion..."
  VAULT="$VAULT" SOURCES="$SOURCES_DIR" TAG="$TAG" RESULTS="$RESULTS" \
    python3 "$BENCH_DIR/versions/v8-citations.py" 2>&1 | grep -E "^  [✓⚠✗=]|Zitationsrate"

  echo "  Widerspruchserkennung..."
  VAULT="$VAULT" TAG="$TAG" RESULTS="$RESULTS" \
    python3 "$BENCH_DIR/versions/v8-contradictions.py" 2>&1 | grep -E "^  [✓⚠✗=]|Widersprüche|Geprüfte"

  # Groundedness-Score aus v8 auslesen (für LWQS v8)
  V8_GROUND_SCORE=$(tail -n +2 "$GROUND_TSV" 2>/dev/null | awk -F'\t' '{s+=$6; n++} END{if(n>0) printf "%.1f", s/n*100; else print "0"}')
  V8_UNGROUND_RATE=$(tail -n +2 "$GROUND_TSV" 2>/dev/null | awk -F'\t' '$6<0.5{n++} END{printf "%.1f", n/NR*100}')
  V8_CITE_TSV="$RESULTS/${TAG}-v8-citations.tsv"
  V8_CITE_RATE=$(tail -n +2 "$V8_CITE_TSV" 2>/dev/null | awk -F'\t' '$3=="quellenbelegt"{n++} END{if(NR>0) printf "%.1f", n/NR*100; else print "0"}')
  V8_CONTRA_TSV="$RESULTS/${TAG}-v8-contradictions.tsv"
  V8_CONTRA_COUNT=$(tail -n +2 "$V8_CONTRA_TSV" 2>/dev/null | wc -l)

  echo "  ✓ Groundedness: ${V8_GROUND_SCORE}%"
  echo "  ✓ Zitationsrate: ${V8_CITE_RATE}%"
  echo "  ✓ Widersprüche: ${V8_CONTRA_COUNT}"
fi

# ═══════════════════════════════════════════════════════════
# SCHRITT 4: LWQS BERECHNEN
# ═══════════════════════════════════════════════════════════
echo ""
echo "━━━ Schritt 4/6: LWQS ━━━"

LWQS=$(python3 -c "
# Inhaltliche Fehlerrate (ohne Self-Refs als mechanisch)
inhaltlich = ($K_COUNT + $H_COUNT + $N_COUNT + $S_COUNT) / $TOTAL_CHECK * 100
precision = 100 - inhaltlich

# Vernetzung (15 WL/Page = 100%)
vernetzung = min($WL_PER_PAGE / 15 * 100, 100)

# Vollständigkeit (80 Pages = 100%)
vollstaendigkeit = min($TOTAL_PAGES / 80 * 100, 100)

# Anti-Halluzination (5x Strafe)
anti_h = max(100 - $H_RATE * 5, 0)

# Mechanisch (Self-Refs, Truncation, Underscores)
mech = 100
if $SELF_REF_RATE > 5: mech -= 33
if $T_COUNT > 0: mech -= 33
if $UNDERSCORES > $TOTAL_PAGES * 0.05: mech -= 34

lwqs = 0.40 * precision + 0.25 * vernetzung + 0.15 * vollstaendigkeit + 0.15 * anti_h + 0.05 * mech
print(f'{lwqs:.1f}')
" 2>/dev/null || echo "0")

echo "  LWQS: $LWQS / 100"

# V8 LWQS: mit NLI-basiertem Groundedness-Score
if [[ "$V8" == "1" ]] && [[ -n "$V8_GROUND_SCORE" ]]; then
  LWQS_V8=$(python3 -c "
# V8: Groundedness-Score ersetzt LLM-basierte Precision für H
ground_score = ${V8_GROUND_SCORE:-0}  # 0-100
unground_rate = ${V8_UNGROUND_RATE:-0}  # % Pages mit Groundedness < 0.5

# Precision: deterministisch K+N+S + NLI-basiertes H
inhaltlich = ($K_COUNT + $H_COUNT + $N_COUNT + $S_COUNT) / $TOTAL_CHECK * 100
precision = 100 - inhaltlich

# Vernetzung
vernetzung = min($WL_PER_PAGE / 15 * 100, 100)

# Vollständigkeit
vollstaendigkeit = min($TOTAL_PAGES / 80 * 100, 100)

# Anti-Halluzination: NLI-basierte Groundedness < 0.5 Rate × 5
anti_h = max(100 - unground_rate * 5, 0)

# Mechanisch
mech = 100
if $SELF_REF_RATE > 5: mech -= 33
if $T_COUNT > 0: mech -= 33
if $UNDERSCORES > $TOTAL_PAGES * 0.05: mech -= 34

lwqs = 0.40 * precision + 0.25 * vernetzung + 0.15 * vollstaendigkeit + 0.15 * anti_h + 0.05 * mech
print(f'{lwqs:.1f}')
" 2>/dev/null || echo "0")
  echo "  LWQS (v8 NLI): $LWQS_V8 / 100"
fi

# ═══════════════════════════════════════════════════════════
# SCHRITT 5: SELF-REVIEW
# ═══════════════════════════════════════════════════════════
echo ""
echo "━━━ Schritt 5/6: SELF-REVIEW ━━━"

case "$TYPE" in
  claude|claude-agent)
    VAULT="$VAULT" "$BENCH_DIR/opus-review.sh" 2>&1 | tail -5
    ;;
  codex)
    echo "  (Codex Self-Review nicht implementiert — übersprungen)"
    ;;
  ollama)
    VAULT="$VAULT" ENV_FILE="$(cd "$BENCH_DIR" && realpath "$MODEL")" \
      python3 "$BENCH_DIR/versions/v6-review.py" 2>&1 | tail -5
    ;;
esac

REVIEW_TSV="$RESULTS/${TAG}-v6-review.tsv"
if [[ -f "$REVIEW_TSV" ]]; then
  REVIEW_OK=$(tail -n +2 "$REVIEW_TSV" | awk -F'\t' '$2=="True"' | wc -l)
  REVIEW_TOTAL=$(tail -n +2 "$REVIEW_TSV" | wc -l)
  echo "  Self-Review: $REVIEW_OK/$REVIEW_TOTAL OK"
else
  REVIEW_OK="n/a"
  REVIEW_TOTAL="n/a"
  echo "  Self-Review: übersprungen"
fi

# ═══════════════════════════════════════════════════════════
# SCHRITT 6: REPORT
# ═══════════════════════════════════════════════════════════
echo ""
echo "━━━ Schritt 6/6: REPORT ━━━"

cat > "$REPORT" <<EOF
# Benchmark-Report: $TAG ($MODEL)

> Generiert: $(date)
> Typ: $TYPE | Schema: $SCHEMA
> Vault: $VAULT

## Ergebnis

| Metrik | Wert |
|---|---|
| **LWQS** | **$LWQS / 100** |
| Pages | $TOTAL_PAGES |
| Wikilinks | $TOTAL_WL ($WL_PER_PAGE/Page) |
| Fehlerrate | $FEHLERRATE % |
| Halluzinations-Rate | $H_RATE % |
| Self-References | $SELF_REFS ($SELF_REF_RATE %) |
| Self-Review OK | $REVIEW_OK / $REVIEW_TOTAL |

## Page-Verteilung

| Typ | Anzahl |
|---|---|
| Summaries | $summaries_count |
| Entities | $entities_count |
| Concepts | $concepts_count |
| Tools | $tools_count |
| Procedures | $procedures_count |
| Topics | $topics_count |

## Fehlertypen

| Typ | Anzahl | Rate |
|---|---|---|
| Klassifikation (K) | $K_COUNT | $(echo "scale=1; $K_COUNT*100/$TOTAL_CHECK" | bc)% |
| Halluzination (H) | $H_COUNT | ${H_RATE}% |
| Naming (N) | $N_COUNT | $(echo "scale=1; $N_COUNT*100/$TOTAL_CHECK" | bc)% |
| Stub (S) | $S_COUNT | $(echo "scale=1; $S_COUNT*100/$TOTAL_CHECK" | bc)% |
| Truncation (T) | $T_COUNT | $(echo "scale=1; $T_COUNT*100/$TOTAL_CHECK" | bc)% |
| Self-Reference (R) | $R_COUNT | $(echo "scale=1; $R_COUNT*100/$TOTAL_CHECK" | bc)% |

## Dateien

- Ingest-Stats: \`$RESULTS/${TAG}-v5-ingest.tsv\`
- Metriken: \`$RESULTS/${TAG}-v5-metrics.tsv\`
- Fehlerraten: \`$RESULTS/${TAG}-fehlerraten.tsv\`
- Self-Review: \`$RESULTS/${TAG}-v6-review.tsv\`
- Vault: \`$VAULT/wiki/\`
EOF

# V8-Abschnitt anhängen wenn vorhanden
if [[ "$V8" == "1" ]] && [[ -f "$GROUND_TSV" ]]; then
  cat >> "$REPORT" <<EOF

## V8 RAG-Techniken

| Metrik | Wert |
|---|---|
| **LWQS (v8 NLI)** | **${LWQS_V8:-n/a} / 100** |
| Groundedness-Score | ${V8_GROUND_SCORE:-n/a}% |
| Zitationsrate | ${V8_CITE_RATE:-n/a}% |
| Widersprüche | ${V8_CONTRA_COUNT:-0} |
| Halluzinationsmethode | NLI (cross-encoder/nli-deberta-v3-base) |

### V8-Dateien

- Groundedness: \`$RESULTS/${TAG}-v8-groundedness.tsv\`
- Groundedness-Detail: \`$RESULTS/${TAG}-v8-groundedness-detail.tsv\`
- Zitationen: \`$RESULTS/${TAG}-v8-citations.tsv\`
- Widersprüche: \`$RESULTS/${TAG}-v8-contradictions.tsv\`
EOF
fi

echo "  Report: $REPORT"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  FERTIG: $TAG                                       ║"
echo "║  LWQS: $LWQS / 100                                  ║"
if [[ "$V8" == "1" ]] && [[ -n "$LWQS_V8" ]]; then
echo "║  LWQS (v8 NLI): $LWQS_V8 / 100                     ║"
fi
echo "║  Pages: $TOTAL_PAGES | WL/Page: $WL_PER_PAGE        ║"
echo "║  Fehlerrate: $FEHLERRATE% | Halluz: $H_RATE%        ║"
echo "║  Ende: $(date)                                      ║"
echo "╚══════════════════════════════════════════════════════╝"

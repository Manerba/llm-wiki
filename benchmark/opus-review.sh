#!/usr/bin/env bash
# opus-review.sh — v6 Self-Review der Opus-Wiki-Pages via Claude Code CLI
#
# Nutzt `claude -p` (non-interaktiv, print mode) mit max effort.
# Jede Page wird einzeln reviewt und ggf. korrigiert.
#
# Usage:
#   ./opus-review.sh                    # reviewt /opt/llm-wiki/wiki/
#   VAULT=/opt/llm-wiki ./opus-review.sh
#
set -uo pipefail

VAULT="${VAULT:-/opt/llm-wiki}"
WIKI="$VAULT/wiki"
RESULTS_DIR="/opt/llm-wiki/benchmark/results"
REPORT="$RESULTS_DIR/opus-v6-review.tsv"
LOG="/tmp/opus-v6-review.log"

mkdir -p "$RESULTS_DIR"
echo > "$LOG"
printf "page\tok\tissue_count\tissues\twl_added\tduration_s\n" > "$REPORT"

# Sammle alle existierenden Page-Namen
EXISTING_PAGES=""
while IFS= read -r -d '' f; do
  name=$(basename "$f" .md)
  EXISTING_PAGES="$EXISTING_PAGES, [[${name}]]"
done < <(find "$WIKI" -name "*.md" -print0 2>/dev/null)
EXISTING_PAGES="${EXISTING_PAGES:2}"  # Leading ", " entfernen

TOTAL=0
OK_COUNT=0
FIXES=0

while IFS= read -r -d '' page_file; do
  rel="${page_file#$VAULT/}"
  TOTAL=$((TOTAL + 1))
  page_content=$(head -c 8000 "$page_file")
  page_name=$(basename "$page_file" .md)

  echo "[$(date +%H:%M:%S)] [$TOTAL] $rel" | tee -a "$LOG"

  PROMPT="Du bist ein Wiki-Qualitätsprüfer für ein LLM-Wiki nach Karpathys Pattern.

Prüfe die folgende Wiki-Page auf diese Fehler:

1. KLASSIFIKATION: Ist der Page-Typ korrekt?
   - entity = Personen, Organisationen, Hersteller (NICHT Protokolle, Standards, Mechanismen)
   - concept = Protokolle, Begriffe, Standards, RFCs, Architekturen, Mechanismen
   - tool = konkrete Software/Hardware (ip6tables, radvd, FRITZ!Box)
   - procedure = Schritt-für-Schritt-Anleitung
   - summary = Quellen-Zusammenfassung
   - topic = Themenübersicht

2. HALLUZINATION: Steht der Inhalt wirklich in den verlinkten Quellen?

3. FEHLENDE WIKILINKS: Werden existierende Pages im Text erwähnt ohne [[Wikilink]]?

4. STUB: Weniger als 3 Sätze Body?

5. FRONTMATTER: type, aliases, tags, created, updated, sources vorhanden?

EXISTIERENDE PAGES im Wiki:
$EXISTING_PAGES

PAGE ZU PRÜFEN ($rel):
\`\`\`
$page_content
\`\`\`

ANTWORTE NUR mit einem JSON-Objekt (kein Markdown-Codeblock, keine Erklärung):
{
  \"ok\": true/false,
  \"issues\": [\"issue1\", \"issue2\"] oder [],
  \"missing_wikilinks\": [\"Page A\", \"Page B\"] oder [],
  \"classification_fix\": \"concepts\" oder null,
  \"fix_description\": \"was korrigiert werden sollte\" oder null
}"

  T_START=$(date +%s%N)
  REVIEW=$(echo "$PROMPT" | claude -p --model claude-opus-4-6 2>>"$LOG") || true
  T_END=$(date +%s%N)
  DURATION=$(echo "scale=1; ($T_END - $T_START) / 1000000000" | bc)

  # JSON parsen
  IS_OK=$(echo "$REVIEW" | python3 -c "
import json, sys, re
text = sys.stdin.read().strip()
text = re.sub(r'^[^\{]*', '', text)
text = re.sub(r'[^\}]*$', '', text)
try:
    r = json.loads(text + '}' if not text.endswith('}') else text)
    ok = r.get('ok', True)
    issues = r.get('issues', [])
    missing = r.get('missing_wikilinks', [])
    print(f'{ok}\t{len(issues)}\t{\"|\".join(issues) if issues else \"OK\"}\t{len(missing)}')
except:
    print(f'True\t0\tparse_error\t0')
" 2>/dev/null)

  PAGE_OK=$(echo "$IS_OK" | cut -f1)
  ISSUE_COUNT=$(echo "$IS_OK" | cut -f2)
  ISSUES=$(echo "$IS_OK" | cut -f3)
  WL_ADDED=$(echo "$IS_OK" | cut -f4)

  [[ "$PAGE_OK" == "True" ]] && OK_COUNT=$((OK_COUNT + 1))

  # Fehlende Wikilinks nachtragen
  if [[ "$WL_ADDED" -gt 0 ]]; then
    MISSING_PAGES=$(echo "$REVIEW" | python3 -c "
import json, sys, re
text = sys.stdin.read().strip()
text = re.sub(r'^[^\{]*', '', text)
text = re.sub(r'[^\}]*$', '', text)
try:
    r = json.loads(text + '}' if not text.endswith('}') else text)
    for p in r.get('missing_wikilinks', []):
        print(p)
except:
    pass
" 2>/dev/null)

    while IFS= read -r missing_page; do
      [[ -z "$missing_page" ]] && continue
      # Nur einfügen wenn nicht schon verlinkt
      if ! grep -q "\[\[$missing_page\]\]" "$page_file" 2>/dev/null; then
        # Erstes Vorkommen im Body (nach Frontmatter) ersetzen
        python3 -c "
import re, sys
content = open('$page_file').read()
parts = content.split('---', 2)
if len(parts) >= 3:
    body = parts[2]
    pattern = re.compile(r'(?<!\[)(' + re.escape('$missing_page') + r')(?!\])', re.IGNORECASE)
    new_body, n = pattern.subn(r'[[\1]]', body, count=1)
    if n > 0:
        open('$page_file', 'w').write(parts[0] + '---' + parts[1] + '---' + new_body)
        sys.exit(0)
sys.exit(1)
" 2>/dev/null && FIXES=$((FIXES + 1))
      fi
    done <<< "$MISSING_PAGES"
  fi

  STATUS_CHAR="✓"
  [[ "$PAGE_OK" != "True" ]] && STATUS_CHAR="✗"
  echo "  $STATUS_CHAR $ISSUES (${DURATION}s, +${WL_ADDED} wl)" | tee -a "$LOG"

  printf "%s\t%s\t%s\t%s\t%s\t%s\n" "$rel" "$PAGE_OK" "$ISSUE_COUNT" "$ISSUES" "$WL_ADDED" "$DURATION" >> "$REPORT"

done < <(find "$WIKI" -name "*.md" -print0 2>/dev/null | sort -z)

echo ""
echo "════════════════════════════════════════"
echo "  Opus v6 Self-Review fertig"
echo "  $OK_COUNT/$TOTAL OK ($(echo "scale=1; $OK_COUNT * 100 / $TOTAL" | bc)%)"
echo "  Wikilink-Fixes: $FIXES"
echo "  Report: $REPORT"
echo "════════════════════════════════════════"

#!/usr/bin/env bash
# codex-ingest.sh — Ingest einer Quelle via OpenAI Codex CLI (gpt-5.4) über SSH.
#
# Nutzt `codex exec` non-interaktiv auf dem Remotehost 10.0.12.20.
# Codex arbeitet als Agent (liest CLAUDE.md, liest Quelle, schreibt Wiki-Pages
# als Dateien) — identisch zum Claude-Code-Workflow, nur mit anderem Modell.
#
# Usage:
#   ./codex-ingest.sh <quelle>                    # einzelne Quelle
#   ./codex-ingest.sh raw/converted/IPv6/IPv6.md   # relativ zum Vault
#
# Env-Variablen:
#   VAULT       Vault-Pfad auf dem LOKALEN Host (default: /opt/llm-wiki-bench-gpt54)
#   REMOTE_HOST SSH-Ziel (default: root@10.0.12.20)
#   MODEL       Codex-Modell (default: gpt-5.4)
#   DRY_RUN     1 = Prompt zeigen, nicht ausführen
#
# Voraussetzungen:
#   - Codex CLI auf dem Remotehost installiert + eingeloggt (codex login)
#   - SSH-Key-Auth zum Remotehost
#   - Vault-Verzeichnis muss auf dem Remotehost existieren (wird via rsync gesynct)
#   - CLAUDE.md und raw/converted/ müssen im Vault vorhanden sein
#
set -euo pipefail

VAULT="${VAULT:-/opt/llm-wiki-bench-gpt54}"
REMOTE_HOST="${REMOTE_HOST:-root@10.0.12.20}"
MODEL="${MODEL:-gpt-5.4}"
DRY_RUN="${DRY_RUN:-0}"
SOURCE="${1:?Usage: codex-ingest.sh <quelle-relativ-zum-vault>}"

# Absoluter Quellpfad
if [[ "$SOURCE" != /* ]]; then
  SOURCE_ABS="$VAULT/$SOURCE"
else
  SOURCE_ABS="$SOURCE"
  SOURCE="${SOURCE#$VAULT/}"
fi

if [[ ! -f "$SOURCE_ABS" ]]; then
  echo "ERROR: Quelle nicht gefunden: $SOURCE_ABS" >&2
  exit 1
fi

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
RESULT_DIR="$VAULT/benchmark-results"
mkdir -p "$RESULT_DIR"
OUTPUT_FILE="$RESULT_DIR/codex-${TIMESTAMP}-$(basename "$SOURCE" .md).txt"
LOG_FILE="$RESULT_DIR/codex-${TIMESTAMP}-$(basename "$SOURCE" .md).log"

# ── Vault auf Remotehost syncen ──
echo "[$(date +%H:%M:%S)] Synce Vault nach $REMOTE_HOST:$VAULT ..."
ssh "$REMOTE_HOST" "mkdir -p $VAULT/{wiki/{topics,entities,concepts,tools,procedures,summaries},raw}" 2>/dev/null

# CLAUDE.md syncen
rsync -az \
  "$VAULT/CLAUDE.md" \
  "$REMOTE_HOST:$VAULT/" 2>>"$LOG_FILE"

# Dangling Symlinks auf dem Remote aufräumen und Quelle direkt syncen
SOURCE_DIR=$(dirname "$SOURCE")
ssh "$REMOTE_HOST" "rm -f $VAULT/raw/converted $VAULT/raw/kb 2>/dev/null; mkdir -p $VAULT/$SOURCE_DIR" 2>>"$LOG_FILE"
rsync -azL \
  "$SOURCE_ABS" \
  "$REMOTE_HOST:$VAULT/$SOURCE" 2>>"$LOG_FILE"

# Wiki-Verzeichnis syncen (damit Codex existierende Pages sieht) — mit -L für Symlink-Dereferenzierung
rsync -azL \
  "$VAULT/wiki/" \
  "$REMOTE_HOST:$VAULT/wiki/" 2>>"$LOG_FILE"

# Git-Repo auf Remote initialisieren falls nötig
ssh "$REMOTE_HOST" "cd $VAULT && git init -q 2>/dev/null; git add -A 2>/dev/null; git commit -q -m 'pre-ingest sync' 2>/dev/null || true" 2>>"$LOG_FILE"

echo "[$(date +%H:%M:%S)] Sync fertig."

# ── Ingest-Prompt bauen ──
EXISTING_PAGES=$(find "$VAULT/wiki" -name "*.md" -exec basename {} .md \; 2>/dev/null | sort -u | head -60 | paste -sd, -)

PROMPT="Du bist in einem LLM-Wiki-Vault. Lies zuerst CLAUDE.md — das ist das Schema.

Dann ingestiere die Quelle: $SOURCE

Gehe dabei GENAU nach dem Ingest-Workflow in CLAUDE.md vor:
1. Lies die Quelle vollständig
2. Erstelle eine Summary-Page in wiki/summaries/
3. Leite Entities, Concepts, Tools und Procedures ab — schreibe jede als eigene Datei
4. Setze [[Wikilinks]] im Body-Text
5. Jede Page bekommt YAML-Frontmatter (type, aliases, tags, created, updated, sources)
6. sources im Frontmatter IMMER als Block-Liste mit String-Quotes

Bereits existierende Pages (für Wikilinks): $EXISTING_PAGES

Schreibe die Pages direkt als Dateien. Halluziniere nicht — nur was in der Quelle steht.
Dateinamen: Klartext mit Spaces, deutsch."

if [[ "$DRY_RUN" == "1" ]]; then
  echo "=== DRY RUN — Prompt: ==="
  echo "$PROMPT"
  echo "=== Würde ausführen: ==="
  echo "ssh $REMOTE_HOST 'cd $VAULT && codex exec --full-auto --ephemeral -m $MODEL -o ... \"...\"'"
  exit 0
fi

# ── Codex exec über SSH ──
echo "[$(date +%H:%M:%S)] Starte Codex exec ($MODEL) für: $SOURCE"
echo "[$(date +%H:%M:%S)] Remote: $REMOTE_HOST:$VAULT"

T_START=$(date +%s)

ssh "$REMOTE_HOST" "cd $VAULT && codex exec \
  --full-auto \
  --ephemeral \
  -m $MODEL \
  -o /tmp/codex-output-$TIMESTAMP.txt \
  \"$PROMPT\"" 2>&1 | tee -a "$LOG_FILE"

T_END=$(date +%s)
DURATION=$((T_END - T_START))

echo ""
echo "[$(date +%H:%M:%S)] Codex fertig ($DURATION s). Synce Ergebnisse zurück..."

# ── Ergebnisse zurück syncen ──
rsync -az \
  "$REMOTE_HOST:$VAULT/wiki/" \
  "$VAULT/wiki/" 2>>"$LOG_FILE"

# Codex-Output holen
scp "$REMOTE_HOST:/tmp/codex-output-$TIMESTAMP.txt" "$OUTPUT_FILE" 2>>"$LOG_FILE" || true

# ── Statistik ──
PAGES_AFTER=$(find "$VAULT/wiki" -name "*.md" | wc -l)
WL_TOTAL=$(find "$VAULT/wiki" -name "*.md" -exec grep -oh '\[\[' {} + 2>/dev/null | wc -l)

echo ""
echo "═══════════════════════════════════════════"
echo "  CODEX INGEST ERGEBNIS"
echo "═══════════════════════════════════════════"
echo "  Modell:    $MODEL"
echo "  Quelle:    $SOURCE"
echo "  Dauer:     ${DURATION}s"
echo "  Pages:     $PAGES_AFTER"
echo "  Wikilinks: $WL_TOTAL"
echo "  Output:    $OUTPUT_FILE"
echo "  Log:       $LOG_FILE"
echo "═══════════════════════════════════════════"

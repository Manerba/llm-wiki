#!/usr/bin/env bash
# convert-raw.sh — Konvertiert Quellen aus raw/kb/ zu Markdown in raw/converted/.
# Idempotent: skipt Files, bei denen das Ziel neuer ist als die Quelle.
# Filter-Whitelist: docx doc odt rtf txt md pdf epub xlsx xlsm xls pptx ppt
# Ignoriert Office-Lockfiles (~$*) und Verzeichnisse aus IGNORE_DIRS.
#
# Usage:
#   convert-raw.sh                       # alle Quellen
#   convert-raw.sh IPv6                  # nur ein Topic-Verzeichnis
#   convert-raw.sh IPv6 Proxmox          # mehrere Topic-Verzeichnisse
#   DRY_RUN=1 convert-raw.sh             # nur zeigen, was gemacht würde
#   OCR=1 convert-raw.sh                 # PDFs vor pdftotext durch OCR jagen
#
set -uo pipefail

VAULT="/opt/llm-wiki"
SRC_ROOT="$VAULT/raw/kb"
DST_ROOT="${DST_ROOT:-$VAULT/raw/converted}"
LOG="$VAULT/raw/converted/convert.log"
DRY_RUN="${DRY_RUN:-0}"
OCR="${OCR:-0}"
CONVERTER="${CONVERTER:-auto}"  # "docling", "local", "auto" (docling mit fallback)
DOCLING_URL="${DOCLING_URL:-http://10.0.12.16:5001}"
DOCLING_TIMEOUT="${DOCLING_TIMEOUT:-300}"

# Verzeichnisse, die NICHT ingested werden sollen (Build-Artefakte, Code-Trees)
IGNORE_DIRS=(
  "Visual Basic .NET"
  "Visual Basic"
  "Handbücher/Sichere Windows-Infrastrukturen/Codebeispiele und GPOs"
  "node_modules"
  ".git"
)

mkdir -p "$DST_ROOT"
exec 3>>"$LOG"
log()  { echo "[$(date '+%F %T')] $*" >&3; }
note() { echo "[$(date '+%F %T')] $*"; echo "[$(date '+%F %T')] $*" >&3; }

# --- Filter: Soll dieser Pfad ingested werden? ---
should_skip_dir() {
  local rel="$1"
  for ig in "${IGNORE_DIRS[@]}"; do
    [[ "$rel" == "$ig" || "$rel" == "$ig"/* ]] && return 0
  done
  return 1
}

needs_update() {
  local src="$1" dst="$2"
  [[ ! -e "$dst" ]] && return 0
  [[ "$src" -nt "$dst" ]] && return 0
  return 1
}

ensure_dir() {
  mkdir -p "$(dirname "$1")"
}

# --- Docling-Konverter (v5 Feature 1) ---

# Formate die Docling versteht
_DOCLING_FORMATS="pdf docx pptx xlsx png tiff tif jpg jpeg"

_is_docling_format() {
  local ext="${1,,}"
  [[ " $_DOCLING_FORMATS " == *" $ext "* ]]
}

convert_docling() {
  local src="$1" dst="$2"
  local response tmpfile
  tmpfile=$(mktemp)

  if ! curl -sf --max-time "$DOCLING_TIMEOUT" \
    -X POST "$DOCLING_URL/v1/convert/file" \
    -F "files=@$src" \
    -F 'options={"to_formats":["md"],"do_ocr":true,"ocr_lang":["de","en"],"table_mode":"accurate"}' \
    -o "$tmpfile" 2>>"$LOG"; then
    rm -f "$tmpfile"
    return 1
  fi

  # Markdown aus JSON extrahieren
  python3 -c "
import json, sys, re
try:
    r = json.load(open('$tmpfile'))
    doc = r.get('document', r)
    md = doc.get('md_content', '') or doc.get('content', '') or ''
    # Base64-Bilder entfernen (zu groß für Wiki-Ingest, werden separat via VLM verarbeitet)
    md = re.sub(r'!\[Image\]\(data:image/[^)]+\)', '[Bild: siehe Original]', md)
    if md.strip():
        print(md)
        sys.exit(0)
    else:
        sys.exit(1)
except Exception:
    sys.exit(1)
" > "$dst" 2>>"$LOG"
  local rc=$?
  rm -f "$tmpfile"
  return $rc
}

convert_with_fallback() {
  # Versucht Docling, fällt auf lokalen Konverter zurück
  local src="$1" dst="$2" ext="$3" local_fn="$4"

  if [[ "$CONVERTER" == "local" ]]; then
    $local_fn "$src" "$dst"
    return $?
  fi

  if _is_docling_format "$ext"; then
    if convert_docling "$src" "$dst"; then
      note "DOCLING $rel"
      return 0
    fi
    if [[ "$CONVERTER" == "docling" ]]; then
      return 1  # kein Fallback
    fi
    # auto: Fallback auf lokal
    log "DOCLING-FALLBACK $rel → lokaler Konverter"
  fi

  $local_fn "$src" "$dst"
}

# --- Konverter pro Format (lokale Fallbacks) ---

convert_text() {
  local src="$1" dst="$2"
  cp "$src" "$dst"
}

convert_pandoc() {
  local src="$1" dst="$2"
  pandoc --wrap=none --from="$3" --to=markdown "$src" -o "$dst" 2>>"$LOG"
}

convert_docx_like() {
  # docx, odt, rtf, epub direkt mit pandoc
  local src="$1" dst="$2"
  pandoc --wrap=none "$src" -o "$dst" 2>>"$LOG"
}

convert_doc() {
  # alte .doc → .docx via libreoffice → markdown via pandoc
  local src="$1" dst="$2"
  local tmpdir=$(mktemp -d)
  if libreoffice --headless --convert-to docx --outdir "$tmpdir" "$src" >>"$LOG" 2>&1; then
    local docx="$tmpdir/$(basename "${src%.*}").docx"
    [[ -f "$docx" ]] && pandoc --wrap=none "$docx" -o "$dst" 2>>"$LOG"
  fi
  rm -rf "$tmpdir"
}

convert_xlsx() {
  # xlsx/xlsm via ssconvert → CSV mit Sheet-Trennung
  local src="$1" dst="$2"
  local tmpdir=$(mktemp -d)
  local stem="$tmpdir/sheet"
  if ssconvert -S "$src" "$stem.%s.csv" >>"$LOG" 2>&1; then
    {
      echo "# $(basename "$src")"
      echo
      for csv in "$tmpdir"/sheet.*.csv; do
        [[ -f "$csv" ]] || continue
        local name=$(basename "$csv" .csv | sed 's/^sheet\.//')
        echo "## Sheet: $name"
        echo
        echo '```csv'
        head -200 "$csv"
        echo '```'
        echo
      done
    } > "$dst"
  fi
  rm -rf "$tmpdir"
}

convert_xls_old() {
  # alte .xls → .xlsx via libreoffice → ssconvert
  local src="$1" dst="$2"
  local tmpdir=$(mktemp -d)
  if libreoffice --headless --convert-to xlsx --outdir "$tmpdir" "$src" >>"$LOG" 2>&1; then
    local xlsx="$tmpdir/$(basename "${src%.*}").xlsx"
    [[ -f "$xlsx" ]] && convert_xlsx "$xlsx" "$dst"
  fi
  rm -rf "$tmpdir"
}

convert_pptx() {
  # pptx via libreoffice → docx → pandoc (lossy aber lesbar)
  local src="$1" dst="$2"
  local tmpdir=$(mktemp -d)
  if libreoffice --headless --convert-to docx --outdir "$tmpdir" "$src" >>"$LOG" 2>&1; then
    local docx="$tmpdir/$(basename "${src%.*}").docx"
    [[ -f "$docx" ]] && pandoc --wrap=none "$docx" -o "$dst" 2>>"$LOG"
  fi
  rm -rf "$tmpdir"
}

convert_pdf() {
  local src="$1" dst="$2"
  if [[ "$OCR" == "1" ]]; then
    local tmppdf=$(mktemp --suffix=.pdf)
    if ocrmypdf --skip-text --quiet -l deu+eng "$src" "$tmppdf" >>"$LOG" 2>&1; then
      pdftotext -layout "$tmppdf" "$dst" 2>>"$LOG"
    else
      pdftotext -layout "$src" "$dst" 2>>"$LOG"
    fi
    rm -f "$tmppdf"
  else
    pdftotext -layout "$src" "$dst" 2>>"$LOG"
  fi
}

# --- Main Loop ---
declare -i total=0 done=0 skipped=0 failed=0

# Build search roots
ROOTS=()
if (( $# > 0 )); then
  for d in "$@"; do ROOTS+=("$SRC_ROOT/$d"); done
else
  ROOTS=("$SRC_ROOT")
fi

while IFS= read -r -d '' src; do
  total+=1
  rel="${src#$SRC_ROOT/}"
  base=$(basename "$src")

  # Office-Lockfiles ignorieren
  [[ "$base" == ~\$* ]] && { skipped+=1; continue; }

  # Verzeichnis-Filter
  reldir="${rel%/*}"
  if should_skip_dir "$reldir"; then skipped+=1; continue; fi

  ext="${base##*.}"
  ext="${ext,,}"

  dst="$DST_ROOT/${rel%.*}.md"

  if ! needs_update "$src" "$dst"; then
    skipped+=1
    continue
  fi

  # Leere Quellen überspringen, nicht konvertieren
  if [[ ! -s "$src" ]]; then
    skipped+=1
    note "EMPTY $rel  (Quelldatei ist leer)"
    continue
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "WOULD: $rel  →  ${dst#$DST_ROOT/}"
    continue
  fi

  ensure_dir "$dst"
  rc=0
  case "$ext" in
    txt|md|markdown)        convert_text       "$src" "$dst" ;;
    docx)                   convert_with_fallback "$src" "$dst" "$ext" convert_docx_like ;;
    odt|rtf|epub)           convert_docx_like  "$src" "$dst" ;;
    doc)                    convert_doc        "$src" "$dst" ;;
    xlsx|xlsm)              convert_with_fallback "$src" "$dst" "$ext" convert_xlsx ;;
    xls)                    convert_xls_old    "$src" "$dst" ;;
    pptx)                   convert_with_fallback "$src" "$dst" "$ext" convert_pptx ;;
    ppt)                    convert_pptx       "$src" "$dst" ;;
    pdf)                    convert_with_fallback "$src" "$dst" "$ext" convert_pdf ;;
    png|jpg|jpeg|tiff|tif)  convert_with_fallback "$src" "$dst" "$ext" convert_text ;;  # Docling für Bilder
    *)                      skipped+=1; continue ;;
  esac
  rc=$?

  if [[ $rc -eq 0 && -s "$dst" ]]; then
    done+=1
    note "OK    $rel"
  else
    failed+=1
    note "FAIL  $rel  (rc=$rc, dst empty=$([[ -s $dst ]]; echo $?))"
  fi
done < <(find "${ROOTS[@]}" -type f \
  \( -iname '*.txt' -o -iname '*.md' -o -iname '*.markdown' \
     -o -iname '*.docx' -o -iname '*.doc' -o -iname '*.odt' -o -iname '*.rtf' -o -iname '*.epub' \
     -o -iname '*.xlsx' -o -iname '*.xlsm' -o -iname '*.xls' \
     -o -iname '*.pptx' -o -iname '*.ppt' \
     -o -iname '*.pdf' \) -print0 2>/dev/null)

echo
echo "=== convert-raw.sh fertig ==="
echo "Geprüft:    $total"
echo "Konvertiert: $done"
echo "Übersprungen (idempotent / lockfile / ignore): $skipped"
echo "Fehler:     $failed"
echo "Log:        $LOG"

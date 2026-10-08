#!/usr/bin/env bash
# Wandelt eine Wiki-Seite (Markdown mit Marp-Frontmatter) in HTML-Slides.
# Aufruf: ./scripts/wiki2slides.sh wiki/concepts/Memex.md
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <wiki/page.md>" >&2
  exit 1
fi

INPUT="$1"
OUTPUT_DIR="docs/slides"
mkdir -p "$OUTPUT_DIR"
BASENAME="$(basename "$INPUT" .md)"
OUTPUT="$OUTPUT_DIR/${BASENAME}.html"

marp --html --allow-local-files --output "$OUTPUT" "$INPUT"
echo "Slides geschrieben nach: $OUTPUT"

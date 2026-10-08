#!/usr/bin/env python3
"""
describe-images.py — Ersetzt Bild-Verweise in konvertierten Markdown-Dateien
durch VLM-Beschreibungen (Vision Language Model).

Sucht nach Patterns wie:
  ![](media/image1.png)
  ![Image](data:image/png;base64,...)
  [Bild: siehe Original]

Extrahiert das Bild (aus Datei oder Base64) und schickt es an ein VLM
(qwen2.5vl:7b auf Ollama), das eine sachliche Beschreibung erzeugt.

Usage:
    ./describe-images.py raw/converted/IPv6/
    ./describe-images.py raw/converted/IPv6/IPv6\ Routing.md
    DRY_RUN=1 ./describe-images.py raw/converted/IPv6/

Voraussetzungen:
    - Ollama mit VLM-Modell auf VLM_URL (Default: http://10.0.12.16:11434)
    - Bilder als Dateien (media/imageN.png relativ zur Quelle) ODER als Base64 im Markdown
    - Optional: pandoc --extract-media für DOCX-Quellen
"""
import argparse
import base64
import hashlib
import json
import logging
import os
import re
import sys
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

VLM_URL = os.environ.get("VLM_URL", "http://10.0.12.16:11434")
VLM_MODEL = os.environ.get("VLM_MODEL", "qwen2.5vl:7b")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
CACHE_DIR = Path(os.environ.get("CACHE_DIR", "/opt/llm-wiki/raw/assets/vlm-cache"))

VLM_PROMPT = (
    "Beschreibe dieses Bild sachlich und knapp auf Deutsch in 2-4 Sätzen. "
    "Was zeigt es? Welche Informationen enthält es? "
    "Wenn es ein technisches Diagramm oder Screenshot ist, beschreibe die Struktur."
)

# Patterns für Bild-Verweise in Markdown
IMG_FILE_RE = re.compile(
    r'!\[([^\]]*)\]\((media/[^)]+|[^)]+\.(?:png|jpg|jpeg|gif|bmp|tiff|tif))\)'
    r'(?:\{[^}]*\})?',  # optionale pandoc-Attribute {width=...}
    re.IGNORECASE,
)
IMG_BASE64_RE = re.compile(
    r'!\[([^\]]*)\]\(data:image/([^;]+);base64,([^)]+)\)',
)
IMG_PLACEHOLDER_RE = re.compile(
    r'\[Bild: siehe Original\]',
)


def describe_with_vlm(image_b64: str) -> str:
    """Schickt ein Base64-kodiertes Bild an das VLM und gibt die Beschreibung zurück."""
    payload = {
        "model": VLM_MODEL,
        "prompt": VLM_PROMPT,
        "images": [image_b64],
        "stream": False,
        "options": {"num_predict": 500, "temperature": 0.2},
    }
    req = urllib.request.Request(
        f"{VLM_URL}/api/generate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
            return result.get("response", "").strip()
    except Exception as e:
        log.warning("VLM-Aufruf fehlgeschlagen: %s", e)
        return ""


def get_cached_description(image_hash: str) -> str | None:
    """Liest eine gecachte VLM-Beschreibung, falls vorhanden."""
    cache_file = CACHE_DIR / f"{image_hash}.txt"
    if cache_file.exists():
        return cache_file.read_text().strip()
    return None


def cache_description(image_hash: str, description: str):
    """Speichert eine VLM-Beschreibung im Cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{image_hash}.txt").write_text(description)


def process_file(md_path: Path, source_dir: Path) -> tuple[int, int]:
    """Verarbeitet eine Markdown-Datei und ersetzt Bild-Verweise.

    Returns: (gefundene_bilder, beschriebene_bilder)
    """
    text = md_path.read_text()
    found = 0
    described = 0

    def replace_file_image(m):
        nonlocal found, described
        alt = m.group(1)
        img_ref = m.group(2)
        found += 1

        # Bild-Datei finden (relativ zur Quelle oder zur Markdown-Datei)
        img_path = None
        for base in [source_dir, md_path.parent]:
            candidate = base / img_ref
            if candidate.exists():
                img_path = candidate
                break

        if img_path is None:
            log.debug("Bild nicht gefunden: %s (in %s)", img_ref, md_path.name)
            return m.group(0)  # unverändert

        # Hash für Cache
        img_bytes = img_path.read_bytes()
        img_hash = hashlib.sha256(img_bytes).hexdigest()[:16]

        # Cache prüfen
        cached = get_cached_description(img_hash)
        if cached:
            described += 1
            return f"> **[Bild: {img_ref}]:** {cached}\n"

        if DRY_RUN:
            return f"> **[Bild: {img_ref}]:** _[VLM-Beschreibung würde hier stehen]_\n"

        # VLM aufrufen
        b64 = base64.b64encode(img_bytes).decode()
        desc = describe_with_vlm(b64)
        if desc:
            cache_description(img_hash, desc)
            described += 1
            return f"> **[Bild: {img_ref}]:** {desc}\n"
        return m.group(0)

    def replace_base64_image(m):
        nonlocal found, described
        alt = m.group(1)
        fmt = m.group(2)
        b64_data = m.group(3)
        found += 1

        img_hash = hashlib.sha256(b64_data.encode()[:1000]).hexdigest()[:16]
        cached = get_cached_description(img_hash)
        if cached:
            described += 1
            return f"> **[Bild]:** {cached}\n"

        if DRY_RUN:
            return f"> **[Bild]:** _[VLM-Beschreibung würde hier stehen]_\n"

        desc = describe_with_vlm(b64_data)
        if desc:
            cache_description(img_hash, desc)
            described += 1
            return f"> **[Bild]:** {desc}\n"
        return "[Bild: Beschreibung nicht verfügbar]"

    # Erst Base64-Bilder, dann Datei-Bilder
    text = IMG_BASE64_RE.sub(replace_base64_image, text)
    text = IMG_FILE_RE.sub(replace_file_image, text)

    if found > 0:
        md_path.write_text(text)
        log.info("  %s: %d Bilder gefunden, %d beschrieben", md_path.name, found, described)

    return found, described


def main():
    ap = argparse.ArgumentParser(description="VLM-Bildbeschreibung für konvertierte Markdown-Dateien")
    ap.add_argument("path", type=Path, help="Markdown-Datei oder Verzeichnis")
    args = ap.parse_args()

    target = args.path
    if target.is_file():
        files = [target]
        source_dir = target.parent
    elif target.is_dir():
        files = sorted(target.glob("**/*.md"))
        source_dir = target
    else:
        sys.exit(f"Pfad nicht gefunden: {target}")

    log.info("VLM: %s/%s", VLM_URL, VLM_MODEL)
    log.info("Dateien: %d", len(files))
    if DRY_RUN:
        log.info("DRY RUN — keine VLM-Aufrufe")

    total_found = 0
    total_described = 0

    for f in files:
        found, described = process_file(f, source_dir)
        total_found += found
        total_described += described

    log.info("Fertig: %d Bilder gefunden, %d beschrieben, %d übersprungen",
             total_found, total_described, total_found - total_described)


if __name__ == "__main__":
    main()

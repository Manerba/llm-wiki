#!/usr/bin/env python3
"""
qwen-ingest-v5.py — v4 + Content-Type-Hint (Feature 3 aus v5-Plan).

Erweiterungen gegenüber v4:
  - Content-Type-Erkennung (prose/tabular/code/structured) vor dem Plan-Pass
  - Content-Type-Hint im Prompt (Anti-Über-Granularität bei Tabellen)
  - Alles andere bleibt identisch zu v4

v4 hatte:
  1. Zwei getrennte Mini-Schemata (STRICT für ≤8B, FREE für ≥10B)
  2. Zweistufiger Workflow: Plan-Pass → Per-Page-Pass
  3. Self-Reference-Filter im Post-Processing
  4. Anti-Tabellen-Split-Regel
  5. Naming-Normalisierung + YAML-Fix (aus v2)

Usage:
    VAULT=/opt/llm-wiki-qwen ENV_FILE=/root/.config/qwen-ingest.env \
      ./qwen-ingest-v3.py raw/converted/IPv6/IPv6.md

    VAULT=/opt/llm-wiki-qwen35 ENV_FILE=/root/.config/qwen35-ingest.env \
      SCHEMA_MODE=free ./qwen-ingest-v3.py raw/converted/IPv6/IPv6.md
"""
import argparse
import json
import os
import re
import sys
import textwrap
import time
import urllib.request
from datetime import datetime
from pathlib import Path

VAULT = Path(os.environ.get("VAULT", "/opt/llm-wiki-qwen"))
ENV_FILE = Path(os.environ.get("ENV_FILE", "/root/.config/qwen-ingest.env"))
SCHEMA_MODE = os.environ.get("SCHEMA_MODE", "strict")  # "strict" oder "free"

# ═══════════════════════════════════════════════════════════════════
# SCHEMA STRICT — für schwache Modelle (≤8B)
# Viele Beispiele, Negativ-Beispiele, strenge Vorgaben
# ═══════════════════════════════════════════════════════════════════
SCHEMA_STRICT = """\
Du pflegst ein Wiki aus Markdown-Dateien (deutsch). Aus einer Quelle erzeugst du Wiki-Pages.

PAGE-TYPEN:
| Typ | Verzeichnis | Beispiele |
|---|---|---|
| summary | wiki/summaries/ | eine Quelle, Kernaussagen |
| concept | wiki/concepts/ | Protokolle, Begriffe, Standards, RFCs |
| entity | wiki/entities/ | Personen, Organisationen, Hersteller, Magazine |
| tool | wiki/tools/ | KONKRETE Software/Hardware: ip6tables, radvd, FRITZ!Box |
| procedure | wiki/procedures/ | Schritt-für-Schritt ("So konfiguriert man X") |
| topic | wiki/topics/ | Übersicht eines Themengebiets |

KLASSIFIKATIONS-REGELN (WICHTIG):
- RFC 5952 → concept (ist ein Standard, KEIN Tool, KEIN Entity)
- SLAAC, DHCPv6, NDP → concept (sind Protokolle, KEINE Tools)
- "DHCP Agent", "DHCP Server" → concept (abstrakte Komponenten, KEINE Entities)
- ip6tables, radvd, FRITZ!Box → tool (konkrete Programme/Produkte)
- BSI, IETF, IANA, AVM, Eric Amberg → entity (Organisationen, Personen)

ANTI-HALLUZINATION:
- Schreibe NUR über Inhalte, die WÖRTLICH in der Quelle stehen
- Erfinde KEINE Tools, Procedures oder Fakten
- Tabelleneinträge (einzelne IP-Adressen, Multicast-Gruppen, Port-Nummern)
  sind KEINE eigenen Pages — sie gehören als Markdown-Tabelle in eine
  übergeordnete Concept-Page

DATEINAMEN: Klartext mit Spaces, deutsch. "Router Advertisement.md", nicht "Router_Advertisement.md".

FRONTMATTER:
---
type: <typ>
aliases: [Alt]
tags: [t1, t2]
created: 2026-04-12
updated: 2026-04-12
sources:
  - "[[Summary-Name]]"
---

WIKILINKS: Setze aktiv [[Page Name]] im Body-Text, wo andere Pages erwähnt werden.
"""

# ═══════════════════════════════════════════════════════════════════
# SCHEMA FREE — für stärkere Modelle (≥10B)
# Schlank, nur Konventionen, keine Negativ-Beispiele
# ═══════════════════════════════════════════════════════════════════
SCHEMA_FREE = """\
Du pflegst ein vernetztes Wiki aus Markdown-Dateien. Sprache: Deutsch.

Page-Typen: summary, concept, entity, tool, procedure, topic — in den
gleichnamigen Unterverzeichnissen von wiki/.

Konventionen:
- Dateinamen: Klartext mit Spaces, deutsch
- Frontmatter: type, aliases, tags, created, updated, sources (Block-Liste mit "[[…]]")
- Wikilinks [[Page Name]] aktiv im Body setzen
- Halluziniere nicht — nur was in der Quelle steht
- Tabelleneinträge (einzelne Adressen, Ports) sind keine eigenen Pages,
  sondern gehören in eine übergeordnete Concept-Page als Markdown-Tabelle
- Pro genannter Person/Organisation/Hersteller: eine Entity-Page anlegen
- Procedures nur wenn die Quelle Schritt-für-Schritt-Inhalt hat
- Lege so viele Pages an, wie die Quelle hergibt — keine künstliche Begrenzung
"""

# ═══════════════════════════════════════════════════════════════════
# PLAN-PASS PROMPT
# ═══════════════════════════════════════════════════════════════════
PLAN_PROMPT_TEMPLATE = """\
Lies die folgende Quelle und erstelle einen PLAN: Welche Wiki-Pages sollen
daraus entstehen? Antworte NUR mit einer nummerierten Liste im Format:

1. wiki/<typ>/<Page Name>.md — kurze Beschreibung (1 Satz)
2. wiki/<typ>/<Page Name>.md — ...

Keine Erklärung, kein Vor-/Nachwort. Nur die Liste.

Quellen-Pfad: {source_rel}

Quellen-Inhalt:

```
{source_text}
```
"""

# ═══════════════════════════════════════════════════════════════════
# PER-PAGE PROMPT
# ═══════════════════════════════════════════════════════════════════
PAGE_PROMPT_TEMPLATE = """\
Schreibe jetzt die folgende Wiki-Page. Antworte NUR mit dem vollständigen
Page-Inhalt (Frontmatter + Body), NICHTS anderes.

Page: {page_path}
Beschreibung: {page_desc}
Quellen-Pfad: {source_rel}

Existierende Pages im Wiki (für Wikilinks):
{existing_pages_str}

Quellen-Inhalt:

```
{source_text}
```
"""


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def load_env(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def list_existing_pages(vault: Path) -> list[str]:
    pages = []
    for p in (vault / "wiki").rglob("*.md"):
        rel = str(p.relative_to(vault / "wiki"))
        if rel.startswith(("v1", "v2", "v3")):
            continue
        pages.append(p.stem)
    return sorted(set(pages))


def call_ollama(env: dict, system: str, user: str,
                max_tokens: int = 4000, num_ctx: int = 32768) -> tuple[str, dict]:
    base = env["QWEN_BASE_URL"].rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    url = f"{base}/api/chat"
    payload = {
        "model": env["QWEN_MODEL"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {
            "num_ctx": num_ctx,
            "num_predict": max_tokens,
            "temperature": 0.2,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=900) as resp:
        body = resp.read().decode("utf-8")
    dt = time.time() - t0
    obj = json.loads(body)
    content = obj.get("message", {}).get("content", "") or ""
    stats = {
        "duration_s": dt,
        "prompt_tokens": obj.get("prompt_eval_count"),
        "completion_tokens": obj.get("eval_count"),
        "total_tokens": (obj.get("prompt_eval_count") or 0) + (obj.get("eval_count") or 0),
    }
    return content, stats


# ═══════════════════════════════════════════════════════════════════
# v5-NEU: Content-Type-Detection (Feature 3)
# ═══════════════════════════════════════════════════════════════════

def detect_content_type(text: str) -> str:
    """Erkennt den dominanten Inhaltstyp eines Quelltexts.
    Returns: 'prose', 'tabular', 'code', 'structured', 'unknown'."""
    lines = text.strip().splitlines()
    if not lines:
        return "unknown"
    total = len(lines)
    pipe_lines = sum(1 for l in lines if "|" in l)
    kv_lines = sum(1 for l in lines if re.match(r'^[^:=]{2,30}\s*[:=]\s*.+', l))
    code_lines = sum(1 for l in lines if re.match(r'^\s*[{}\[\]();#//]', l))
    avg_len = sum(len(l) for l in lines) / total if total else 0
    csv_lines = sum(1 for l in lines if l.count(",") >= 3)

    if pipe_lines / total > 0.4 or csv_lines / total > 0.4:
        return "tabular"
    if code_lines / total > 0.3:
        return "code"
    if kv_lines / total > 0.5:
        return "structured"
    if kv_lines / total > 0.3 and avg_len < 50:
        return "structured"
    return "prose"


CONTENT_HINTS = {
    "tabular": (
        "\n\nHINWEIS: Diese Quelle ist eine TABELLE oder Referenzliste. "
        "Fasse die gesamte Tabelle als EINE Concept-Page zusammen (mit Markdown-Tabelle im Body). "
        "Erstelle KEINE eigene Page pro Tabellenzeile oder Eintrag. "
        "Die einzelnen Einträge sind Datenpunkte, keine Konzepte.\n"
    ),
    "code": (
        "\n\nHINWEIS: Diese Quelle enthält hauptsächlich CODE oder Konfiguration. "
        "Erstelle Procedure-Pages für die Konfigurationsschritte und "
        "Tool-Pages für die verwendeten Programme. "
        "Code-Blöcke im Body beibehalten.\n"
    ),
    "structured": (
        "\n\nHINWEIS: Diese Quelle ist STRUKTURIERTER TEXT (Formulare, Listen, Key-Value). "
        "Behalte die Struktur bei und fasse zusammengehörige Abschnitte zusammen.\n"
    ),
    "prose": "",
    "unknown": "",
}


def parse_plan(text: str) -> list[tuple[str, str]]:
    """Parst die nummerierte Liste aus dem Plan-Pass.
    Returns: [(path, description), ...]"""
    entries = []
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r'\d+\.\s*(wiki/.+?\.md)\s*[—–-]\s*(.*)', line)
        if m:
            entries.append((m.group(1), m.group(2).strip()))
    return entries


def normalize_path(path: str) -> str:
    # Singular → Plural für die Standard-Verzeichnisse
    PLURAL_FIX = {
        "wiki/concept/": "wiki/concepts/",
        "wiki/entity/": "wiki/entities/",
        "wiki/summary/": "wiki/summaries/",
        "wiki/tool/": "wiki/tools/",
        "wiki/procedure/": "wiki/procedures/",
        "wiki/topic/": "wiki/topics/",
    }
    for singular, plural in PLURAL_FIX.items():
        if path.startswith(singular):
            path = plural + path[len(singular):]
            break

    p = Path(path)
    stem = p.stem
    suffix = p.suffix or ".md"
    if "_" in stem:
        stem = stem.replace("_", " ")
        stem = re.sub(r"\s+", " ", stem).strip()
    keep = {"IPv6", "IPv4", "TCP", "UDP", "DNS", "DHCP", "DHCPv6", "ICMPv6",
            "BSI", "IETF", "IANA", "RFC", "ULA", "MAC", "MTU", "NAT", "NDP",
            "RA", "SLAAC", "AVM"}
    keep_lower = {k.lower(): k for k in keep}
    words = []
    for w in stem.split():
        if w.lower() in keep_lower:
            words.append(keep_lower[w.lower()])
        elif w[0:1].islower() and len(w) > 2:
            words.append(w[0].upper() + w[1:])
        else:
            words.append(w)
    stem = " ".join(words)
    parts = list(p.parts)
    parts[-1] = stem + suffix
    return str(Path(*parts))


def detect_loop_bug(body: str) -> bool:
    """Erkennt den qwen3:8b Loop-Bug: endlos wiederholte Wikilinks."""
    if len(body) > 10000:
        wl_count = body.count("[[")
        if wl_count > 80:
            return True
    return False


def fix_classification(path: str) -> str:
    """Erweiterte Klassifikations-Korrektur (v4):
    - RFCs → concepts/
    - Protokolle → concepts/
    - Technische Mechanismen (Tunnel, Adresse, Hashwert, EUI) → concepts/
    - Multicast-Adressen (FF..::) → concepts/
    """
    p = Path(path)
    stem = p.stem.lower()

    # v3-Regeln (RFCs + Protokolle)
    if re.match(r"^rfc[\s_-]*\d", stem):
        parts = list(p.parts)
        for i, x in enumerate(parts):
            if x in ("tools", "entities"):
                parts[i] = "concepts"
        return str(Path(*parts))
    protocol_names = {"slaac", "dhcpv6", "stateless dhcp", "stateful dhcp", "ndp", "icmpv6"}
    if stem in protocol_names:
        parts = list(p.parts)
        for i, x in enumerate(parts):
            if x in ("tools", "entities"):
                parts[i] = "concepts"
        return str(Path(*parts))

    # v4-NEU: technische Mechanismen, die als entity landen → concepts
    concept_patterns = [
        r".*tunnel$",           # 6to4-Tunnel, ISATAP-Tunnel
        r".*adresse$",          # Loopback-Adresse, Link-Local-Adresse
        r".*hashwert$",         # SHA1-Hashwert
        r".*eui.*",             # Modified EUI-64
        r"^ff[0-9a-f]{2}::.*", # Multicast-Adressen FF01::1 etc.
        r".*scope$",            # Interface-Local Scope, Site-Local Scope
        r".*header$",           # IPv6 Header, ICMP Header
        r".*prefix$",           # Provider-Prefix
        r".*extension.*",       # Extension Headers
        r".*autoconfiguration$",
        r".*discovery$",        # Path MTU Discovery, Neighbor Discovery
    ]
    for pat in concept_patterns:
        if re.match(pat, stem):
            parts = list(p.parts)
            for i, x in enumerate(parts):
                if x in ("tools", "entities"):
                    parts[i] = "concepts"
            return str(Path(*parts))

    return path


def fix_yaml_sources(body: str) -> str:
    def quote_replacement(m):
        return f'{m.group(1)}- "{m.group(2)}"'
    return re.sub(r'^(\s+)-\s*(\[\[[^\]]+\]\])\s*$', quote_replacement,
                  body, flags=re.MULTILINE)


def remove_self_references(body: str, page_name: str) -> tuple[str, int]:
    """Entfernt [[PageName]] wenn PageName == aktuelle Page."""
    pattern = re.compile(r'\[\[' + re.escape(page_name) + r'\]\]', re.IGNORECASE)
    new_body, count = pattern.subn(page_name, body)
    return new_body, count


def augment_wikilinks(body: str, existing_pages: list[str],
                      current_page: str, max_per_page: int = 10) -> tuple[str, int]:
    """Wandelt Plain-Text → [[Wikilink]], mit Self-Reference-Filter und Limit."""
    if not existing_pages:
        return body, 0
    if body.startswith("---"):
        try:
            _, fm, rest = body.split("---", 2)
            front = "---" + fm + "---"
            text = rest
        except ValueError:
            return body, 0
    else:
        front = ""
        text = body
    pages_sorted = sorted(
        [p for p in existing_pages if p.lower() != current_page.lower()],
        key=lambda x: -len(x),
    )
    count = 0
    for page in pages_sorted:
        if count >= max_per_page:
            break
        if len(page) < 3:
            continue
        escaped = re.escape(page)
        pattern = re.compile(
            r'(?<!\[)(?<!\w)(' + escaped + r')(?!\w)(?!\])',
            re.IGNORECASE,
        )
        match_found = [False]
        def replace_first(m):
            if match_found[0]:
                return m.group(0)
            before = text[:m.start()]
            if before.count("```") % 2 == 1 or before.count("`") % 2 == 1:
                return m.group(0)
            match_found[0] = True
            return f"[[{page}]]"
        new_text = pattern.sub(replace_first, text, count=1)
        if new_text != text:
            count += 1
            text = new_text
    return front + text, count


def is_truncated(body: str) -> bool:
    if not body.startswith("---"):
        return False
    parts = body.split("---", 2)
    if len(parts) < 3:
        return True
    return len(parts[2].strip()) < 50


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Qwen-LLM-Ingest v3 (modell-spezifisch + mehrstufig)")
    ap.add_argument("source", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--save-raw", type=Path)
    ap.add_argument("--plan-tokens", type=int, default=3000)
    ap.add_argument("--page-tokens", type=int, default=4000)
    args = ap.parse_args()

    env = load_env(ENV_FILE)
    schema = SCHEMA_STRICT if SCHEMA_MODE == "strict" else SCHEMA_FREE

    source = args.source
    if not source.is_absolute():
        source = VAULT / source
    if not source.exists():
        sys.exit(f"Quelle nicht gefunden: {source}")
    source_text = source.read_text()
    source_rel = str(source.relative_to(VAULT)) if source.is_relative_to(VAULT) else str(source)
    existing_pages = list_existing_pages(VAULT)

    print(f"Vault:        {VAULT}")
    print(f"Modell:       {env['QWEN_MODEL']}")
    print(f"Schema-Modus: {SCHEMA_MODE}")
    print(f"Quelle:       {source_rel} ({len(source_text)} B)")
    print(f"Existierende Pages: {len(existing_pages)}")

    # ── v5-NEU: Content-Type-Detection ──────────────────────────
    content_type = detect_content_type(source_text)
    content_hint = CONTENT_HINTS.get(content_type, "")
    print(f"Content-Type: {content_type}")

    # ── Phase 1: Plan-Pass ──────────────────────────────────────
    print(f"\n📋 Phase 1: Plan-Pass (max_tokens={args.plan_tokens}) …")
    plan_prompt = content_hint + PLAN_PROMPT_TEMPLATE.format(
        source_rel=source_rel,
        source_text=source_text[:12000],  # Limit falls sehr große Quelle
    )
    plan_raw, plan_stats = call_ollama(env, schema, plan_prompt, args.plan_tokens)
    plan = parse_plan(plan_raw)
    print(f"  ⏱  {plan_stats['duration_s']:.1f} s, prompt={plan_stats['prompt_tokens']}, completion={plan_stats['completion_tokens']}")

    if not plan:
        print(f"  ⚠ Kein Plan erkannt. Roh-Antwort:\n{plan_raw[:500]}")
        sys.exit(2)

    print(f"  📝 {len(plan)} Pages geplant:")
    for path, desc in plan:
        print(f"      {path} — {desc}")

    if args.save_raw:
        (args.save_raw.parent / (args.save_raw.stem + "_plan.txt")).write_text(plan_raw)

    # ── Phase 2: Per-Page-Pass ──────────────────────────────────
    print(f"\n📄 Phase 2: Per-Page-Pass (max_tokens={args.page_tokens}) …")
    existing_str = "\n".join(f"  - [[{p}]]" for p in existing_pages[:60])
    written = []
    total_stats = {"prompt_tokens": plan_stats["prompt_tokens"],
                   "completion_tokens": plan_stats["completion_tokens"],
                   "duration_s": plan_stats["duration_s"],
                   "wikilinks_added": 0, "self_refs_removed": 0,
                   "renamed": 0, "reclassified": 0, "truncated": 0}

    for i, (page_path_raw, page_desc) in enumerate(plan):
        print(f"\n  [{i+1}/{len(plan)}] {page_path_raw}")

        page_prompt = PAGE_PROMPT_TEMPLATE.format(
            page_path=page_path_raw,
            page_desc=page_desc,
            source_rel=source_rel,
            existing_pages_str=existing_str,
            source_text=source_text[:12000],
        )
        page_raw, page_stats = call_ollama(env, schema, page_prompt, args.page_tokens)
        total_stats["prompt_tokens"] = (total_stats["prompt_tokens"] or 0) + (page_stats["prompt_tokens"] or 0)
        total_stats["completion_tokens"] = (total_stats["completion_tokens"] or 0) + (page_stats["completion_tokens"] or 0)
        total_stats["duration_s"] += page_stats["duration_s"]

        print(f"    ⏱ {page_stats['duration_s']:.1f} s, comp={page_stats['completion_tokens']}")

        # Body extrahieren — LLM antwortet manchmal mit Codeblock
        body = page_raw.strip()
        body = re.sub(r"^```(?:markdown|yaml)?\s*\n", "", body)
        body = re.sub(r"\n```\s*$", "", body)

        if not body.startswith("---"):
            # LLM hat kein Frontmatter geschrieben — skip
            print(f"    ⚠ Kein Frontmatter, skip")
            total_stats["truncated"] += 1
            continue

        if is_truncated(body):
            print(f"    ⚠ Truncated, skip")
            total_stats["truncated"] += 1
            continue

        # v4-NEU: Loop-Bug-Detection (qwen3:8b erzeugt manchmal endlose Wikilink-Wiederholungen)
        if detect_loop_bug(body):
            print(f"    ⚠ Loop-Bug (>80 Wikilinks in >10KB), skip")
            total_stats["truncated"] += 1
            continue

        # Post-Processing
        page_path = fix_classification(page_path_raw)
        if page_path != page_path_raw:
            total_stats["reclassified"] += 1

        page_path = normalize_path(page_path)
        if page_path != page_path_raw:
            total_stats["renamed"] += 1

        body = fix_yaml_sources(body)

        page_name = Path(page_path).stem
        body, sr_count = remove_self_references(body, page_name)
        total_stats["self_refs_removed"] += sr_count

        # Wikilinks augmentieren (inklusive der gerade in DIESEM Batch angelegten Pages)
        current_existing = existing_pages + [Path(p).stem for p, _ in plan]
        body, wl_count = augment_wikilinks(body, current_existing, page_name, max_per_page=8)
        total_stats["wikilinks_added"] += wl_count

        if not page_path.startswith("wiki/"):
            print(f"    ⚠ Außerhalb wiki/, skip")
            continue

        target = VAULT / page_path
        if args.dry_run:
            print(f"    WOULD: {target.relative_to(VAULT)} (+{wl_count} wl, -{sr_count} self)")
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
        written.append(target)
        existing_pages.append(page_name)
        print(f"    ✓ {target.relative_to(VAULT)} (+{wl_count} wl)")

    # ── Zusammenfassung ─────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"📊 v3-Ingest abgeschlossen: {source_rel}")
    print(f"   Pages geschrieben: {len(written)}")
    print(f"   Wikilinks augmentiert: {total_stats['wikilinks_added']}")
    print(f"   Self-Refs entfernt: {total_stats['self_refs_removed']}")
    print(f"   Renamed: {total_stats['renamed']}")
    print(f"   Reclassified: {total_stats['reclassified']}")
    print(f"   Truncated/skipped: {total_stats['truncated']}")
    print(f"   ⏱  Total: {total_stats['duration_s']:.1f} s")
    print(f"   📊 Tokens: prompt={total_stats['prompt_tokens']}, completion={total_stats['completion_tokens']}")
    total = len(list((VAULT / 'wiki').rglob('*.md')))
    print(f"   Wiki-Größe: {total} Pages")

    # Log
    if not args.dry_run and written:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        pages_summary = "\n".join(f"- [[{p.stem}]]" for p in written)
        entry = f"""
## [{ts}] ingest-v3 | {source_rel}

**Modell:** {env['QWEN_MODEL']} | **Schema:** {SCHEMA_MODE}
**Plan:** {len(plan)} Pages geplant, {len(written)} geschrieben
**Tokens:** prompt={total_stats['prompt_tokens']}, completion={total_stats['completion_tokens']}
**Dauer:** {total_stats['duration_s']:.1f} s
**Post-Processing:** wl_added={total_stats['wikilinks_added']}, self_refs_removed={total_stats['self_refs_removed']}, renamed={total_stats['renamed']}, reclassified={total_stats['reclassified']}, truncated={total_stats['truncated']}
**Pages ({len(written)}):**
{pages_summary}
"""
        log_file = VAULT / "log.md"
        if log_file.exists():
            log_file.write_text(log_file.read_text() + entry)
        else:
            log_file.write_text(f"# Wiki-Log (v3)\n{entry}")


if __name__ == "__main__":
    main()

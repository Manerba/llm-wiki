# LLM-Wiki Benchmark Framework

Reproduzierbares Test-Framework für den Vergleich verschiedener LLMs beim Wiki-Ingest nach Karpathys Pattern.

## Quick Start: Kompletter Test mit einem Befehl

```bash
cd /opt/llm-wiki/benchmark

# Claude-Familie (Agent-Modus, empfohlen):
./run-full.sh opus claude-opus-4-6 claude-agent
./run-full.sh sonnet claude-sonnet-4-6 claude-agent
./run-full.sh haiku claude-haiku-4-5-20251001 claude-agent

# Claude-Familie (One-Shot, nur für Vergleich):
./run-full.sh opus-oneshot claude-opus-4-6 claude
./run-full.sh sonnet-oneshot claude-sonnet-4-6 claude

# GPT via Codex (Agent-Modus):
./run-full.sh gpt54 gpt-5.4 codex

# Lokales Modell (Ollama):
./run-full.sh qwen3-8b configs/qwen3-8b.env ollama strict
./run-full.sh qwen35 configs/qwen35.env ollama free
./run-full.sh phi4 configs/phi4-14b.env ollama free
```

`run-full.sh` führt automatisch aus:
1. **Ingest** — alle 7 IPv6-Quellen verarbeiten
2. **Metriken** — Pages, Wikilinks, Stubs, Self-Refs, Naming zählen
3. **Fehleranalyse** — deterministisch (K/S/T/R/N) + NLI-Groundedness mit Quellenzugriff (H)
4. **LWQS** — Composite-Score berechnen
5. **v8 RAG-Checks** — Groundedness-Score, Zitationsextraktion, Widerspruchserkennung
6. **Self-Review** — Modell reviewt eigene Pages
7. **Report** — Zusammenfassung als Markdown

## Neues Modell hinzufügen

### Lokales Modell (Ollama)

```bash
# 1. Config anlegen
cat > benchmark/configs/<tag>.env <<EOF
QWEN_BASE_URL=http://10.0.12.16:11434/v1
QWEN_MODEL=<modell-name>
QWEN_API_KEY=not-needed
EOF

# 2. Schema-Wahl: STRICT vs FREE (erste Quelle reicht)
VAULT=/tmp/test-strict ENV_FILE=benchmark/configs/<tag>.env SCHEMA_MODE=strict \
  python3 benchmark/versions/v5.py raw/converted-v5/IPv6/IPv6.md
VAULT=/tmp/test-free ENV_FILE=benchmark/configs/<tag>.env SCHEMA_MODE=free \
  python3 benchmark/versions/v5.py raw/converted-v5/IPv6/IPv6.md
# → Vergleichen: welches liefert mehr korrekte Pages? → Schema-Entscheidung

# 3. Voller Benchmark
./run-full.sh <tag> configs/<tag>.env ollama <strict|free>
```

### Claude-Modell

```bash
# Kein Config nötig — Authentifizierung über claude CLI
./run-full.sh <tag> <model-id> claude-agent
```

### GPT via Codex

```bash
# Codex CLI muss lokal installiert und eingeloggt sein
./run-full.sh <tag> <model-id> codex
```

## Benchmark-Modi

| Typ | Script | Wie das LLM arbeitet |
|---|---|---|
| `claude-agent` | `claude-agent-benchmark.sh` | Agent mit Dateizugriff (liest CLAUDE.md, schreibt Pages als Dateien, iteriert) |
| `claude` | `claude-benchmark.sh` | One-Shot (`claude -p`, ein Aufruf pro Quelle, Parser extrahiert Pages) |
| `codex` | `codex-benchmark.sh` | Agent via Codex CLI (`codex exec --full-auto`) |
| `ollama` | `benchmark.sh` | Batch-Pipeline v5 (Plan-Pass → Per-Page-Pass → Post-Processing) |

**Wichtig:** Agent-Modus (`claude-agent`, `codex`) und One-Shot (`claude`) sind **nicht direkt vergleichbar**. Agent-Modi liefern systematisch bessere Ergebnisse (+12 LWQS-Punkte bei Sonnet). Faire Vergleiche nur innerhalb desselben Modus.

## Benchmark-Stufen

| Stufe | Was | Automatisiert? | Im Master-Runner? |
|---|---|---|---|
| **Ingest** | 7 IPv6-Quellen → Wiki-Pages | ✅ | ✅ Schritt 1 |
| **Metriken** | Pages, WL/Page, Self-Refs, Naming | ✅ deterministisch | ✅ Schritt 2 |
| **Fehleranalyse** | K/S/T/R/N deterministisch, H via NLI mit Quellenzugriff | ✅ | ✅ Schritt 3 |
| **LWQS** | Composite-Score 0–100 | ✅ | ✅ Schritt 4 |
| **v8 RAG-Checks** | Groundedness, Zitationen, Widersprüche | ✅ NLI-basiert | ✅ Schritt 5 |
| **Self-Review (v6)** | Modell reviewt eigene Pages | ✅ | ✅ Schritt 6 |
| **Cross-Review (v9)** | Top-2 Modelle reviewen gegenseitig | ⚠️ manuell | ❌ separat |

### Cross-Review (v9) — Regel

Cross-Review wird **nur zwischen den zwei Modellen mit dem höchsten LWQS** durchgeführt. Begründung: teuer (2× voller Review über alle Pages) und bringt nur Mehrwert wenn die Modelle qualitativ nah beieinander liegen.

## Fehleranalyse (Schritt 3)

Die Fehleranalyse hat zwei Phasen:

### Phase A: Deterministisch (kein LLM, sofort)

| Check | Methode |
|---|---|
| **S** Stub | Sätze im Body zählen (< 3 = Stub) |
| **T** Truncation | Frontmatter geschlossen? `type:` vorhanden? |
| **R** Self-Reference | `[[PageName]]` im eigenen Text? |
| **N** Naming | Underscores im Dateinamen? |
| **K** Klassifikation | Pattern-Match: RFC in tools/entities → Fehler, *Tunnel/*Adresse in entities → Fehler |

### Phase B: NLI-Groundedness mit Quellenzugriff (H)

Das LLM bekommt die Page **UND** alle 7 Originalquellen als Kontext und prüft jede Aussage gegen die Quellen. Alternativ (v8): das NLI-Modell `nli-deberta-v3-base` prüft deterministisch ohne LLM.

**Wichtig:** Ohne Quellenzugriff unterschätzt die Fehleranalyse Halluzinationen dramatisch (14,5 % → 62,5 % bei Opus im Vergleich v1 vs v2 der Analyse).

## v8: RAG-Techniken

Ab v8 läuft zusätzlich zur Fehleranalyse:

| Technik | Script | Was es tut |
|---|---|---|
| **Groundedness** | `v8-groundedness.py` | NLI-Modell prüft jede Aussage gegen Quellen (Score 0–1 pro Satz) |
| **Zitationen** | `v8-citations.py` | Verknüpft Sätze mit konkreten Quellen-Passagen |
| **Widersprüche** | `v8-contradictions.py` | NLI paarweise zwischen thematisch ähnlichen Pages |
| **Re-Ranking** | `v8-rerank.py` | Cross-Encoder nach BM25/Vektor-Retrieval |
| **HyDE** | `v8-hyde.py` | Hypothetische Antwort als Embedding für besseres Retrieval |

Shared Library: `v8_lib.py` (NLI-Modell laden, Satz-Extraktion, TF-IDF Chunk-Retrieval).

NLI-Modelle laufen auf dem KI-Server (10.0.12.16) via Cross-Encoder API.

## LWQS (LLM Wiki Quality Score)

Composite-Score 0–100. Definition: `docs/berichte/LWQS_DEFINITION_2026-04-13.md`

```
LWQS = 0,40 × Precision
     + 0,25 × Vernetzung
     + 0,15 × Vollständigkeit
     + 0,15 × Anti-Halluzination
     + 0,05 × Mechanische Sauberkeit
```

| Komponente | Berechnung |
|---|---|
| Precision (40%) | 100 − inhaltliche Fehlerrate (ohne mechanische Fehler) |
| Vernetzung (25%) | min(WL/Page / 15 × 100, 100) |
| Vollständigkeit (15%) | min(Pages / 80 × 100, 100) |
| Anti-Halluzination (15%) | max(100 − Halluz_Rate × 5, 0) |
| Mechanisch (5%) | 100 − 33×(Self-Ref>5%) − 33×(Truncation>0%) − 34×(Underscore>5%) |

Ab v8 kann die Precision alternativ über den NLI-Groundedness-Score berechnet werden (deterministisch, kein LLM-Bias).

## Verzeichnisstruktur

```
benchmark/
├── README.md                         # Diese Datei
├── run-full.sh                       # Master-Runner (ein Befehl = kompletter Test)
├── benchmark.sh                      # Ingest-Runner für lokale Modelle (Ollama)
├── claude-benchmark.sh               # Ingest-Runner Claude One-Shot
├── claude-agent-benchmark.sh         # Ingest-Runner Claude Agent-Modus
├── codex-benchmark.sh                # Ingest-Runner GPT via Codex
├── codex-ingest.sh                   # Einzelquellen-Ingest via Codex
├── opus-review.sh                    # Self-Review via Claude CLI
├── parse-pages.py                    # Parser für ===PAGE:=== Marker
├── configs/                          # Ein .env pro lokalem Modell
│   ├── qwen3-8b.env
│   ├── qwen35.env
│   └── phi4-14b.env
├── versions/                         # Ingest- und Analyse-Scripte
│   ├── v4.py                         # Ingest: mehrstufig + Post-Processing
│   ├── v5.py                         # Ingest: + Content-Type-Hints
│   ├── v6-review.py                  # Self-Review (lokale Modelle)
│   ├── v8_lib.py                     # Shared Library für v8 RAG-Techniken
│   ├── v8-groundedness.py            # NLI-Groundedness-Check
│   ├── v8-citations.py               # Zitationsextraktion
│   ├── v8-contradictions.py          # NLI-Widerspruchserkennung
│   ├── v8-rerank.py                  # Cross-Encoder Re-Ranking
│   └── v8-hyde.py                    # HyDE Query-Verbesserung
└── results/                          # Automatisch generierte Ergebnisse
    ├── <tag>-v5-ingest.tsv           # Per-Quelle-Stats
    ├── <tag>-v5-metrics.tsv          # Aggregierte Metriken
    ├── <tag>-fehlerraten.tsv         # Fehleranalyse (alt, LLM-basiert)
    ├── <tag>-fehlerraten-v2.tsv      # Fehleranalyse (neu, deterministisch + Quellen)
    ├── <tag>-v8-groundedness.tsv     # NLI-Groundedness pro Page
    ├── <tag>-v8-citations.tsv        # Zitationen pro Satz
    ├── <tag>-v8-contradictions.tsv   # Widersprüche zwischen Pages
    ├── <tag>-v6-review.tsv           # Self-Review-Ergebnisse
    └── <tag>-full-report.md          # Zusammenfassung
```

## Quellen

Alle Benchmarks laufen über dieselben 7 IPv6-Quellen (Docling-konvertiert):

| Quelle | Bytes | Format |
|---|---|---|
| IPv6.md | 883 | docx → Docling |
| IPv6 Adresszusammensetzung.md | 548 | docx → Docling |
| IPv6 Multicast-Adressen.md | 5.828 | xlsx → Docling |
| IPv6 Routing.md | 1.587 | docx → Docling |
| IPv6 Firewall-Konfiguration.md | 20.699 | docx → Docling |
| ipv6_cheat_sheet.md | 14.485 | pdf → Docling |
| ISi-L-IPv6 Leitfaden | 55.767 | pdf → Docling |

Liegen in `raw/converted-v5/IPv6/`. Originale unter `/wiki_raw_data/knowladgebase/IPv6/` (SSHFS).

## Infrastruktur

| Komponente | Details |
|---|---|
| Wiki-Server | Ubuntu 24.04, Obsidian (headless via noVNC :6080), Quartz (:8080), qmd |
| KI-Server | Ubuntu, NVIDIA RTX 3060 12 GB VRAM, Ollama (10.0.12.16:11434) |
| Codex-Host | 10.0.12.20, Codex CLI 0.117.0 (für GPT 5.4) |
| Docling | 10.0.12.16:5001 (für Quellen-Konvertierung, v5+) |
| NLI-Modelle | nli-deberta-v3-base, ms-marco-MiniLM-L-6-v2 (für v8) |

## Versionsverlauf

| Version | Fokus | Status |
|---|---|---|
| v1 | Baseline (One-Shot) | ✅ getestet |
| v2 | Prompting-Optimierung | ✅ getestet |
| v3 | Mehrstufiger Workflow + modell-spezifische Schemata | ✅ getestet |
| v4 | Post-Processing + Benchmark-Framework | ✅ getestet |
| v5 | Input-Qualität (Docling) | ✅ getestet |
| v6 | Self-Review | ✅ getestet |
| v7 | Vektor-Suche im Ingest | 📋 TODO |
| v8 | RAG-Techniken (Groundedness, NLI, Zitationen, Re-Ranking, HyDE) | ✅ implementiert |
| v9 | Cross-Review (nur Top-2 im LWQS) | ✅ getestet |

## Zentrale Erkenntnisse

1. **Workflow > Modell:** Ein 8B-Modell mit v5-Batch (10 WL/Page) schlägt ein Frontier-Modell im One-Shot (6 WL/Page).
2. **Agent-Modus > One-Shot:** Sonnet gewinnt +12 LWQS-Punkte durch Agent-Modus. Dateizugriff + mehrere Turns + eigenes Schreiben macht den Unterschied.
3. **Modell-spezifische Schemata:** ≤8B braucht STRICT, ≥10B braucht FREE. Strenge bei starken Modellen verschlechtert die Ergebnisse.
4. **Fehleranalyse braucht Quellenzugriff:** Ohne Originalquellen unterschätzt der Reviewer Halluzinationen dramatisch (14,5 % → 62,5 % bei Opus).
5. **NLI > LLM für Halluzinations-Erkennung:** Dediziertes NLI-Modell ist deterministisch, reproduzierbar und hat keinen Reviewer-Bias.
6. **Cross-Review ist nicht kommutativ:** GPT findet 96 % Probleme bei Opus, Opus findet 4 % bei GPT. Der strengere Reviewer bestimmt den Befund.
7. **Natürliche Fehlergrenze ~2–5 %:** Semantische Transformation (Paraphrasierung, Klassifikation, Inferenz) erzeugt unvermeidliche Restfehler.

# LLM Wiki — Schema & Konventionen

> Diese Datei steuert, wie der LLM-Agent (Claude Code) dieses Wiki pflegt. Sie ist die wichtigste Konfiguration des Repos. Sie wird mit der Zeit verfeinert — wenn dir Konventionen besser gefallen, ändere sie hier und der nächste Ingest übernimmt sie.
>
> **Pattern-Quelle:** Andrej Karpathy, "LLM Wiki" (https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)

## 1. Drei-Schichten-Architektur

1. **`raw/`** — Quell-Dokumente. **Read-only.** Du liest hier, du schreibst hier nicht.
2. **`wiki/`** — LLM-gepflegte Markdown-Pages. Du besitzt diese Schicht vollständig.
3. **`CLAUDE.md`** (diese Datei), `index.md`, `log.md` — Steuerung & Navigation.

## 2. Sprache & Stil

- **Wiki-Inhalte sind auf Deutsch.** Quellen dürfen in beliebiger Sprache vorliegen — du übersetzt beim Zusammenfassen.
- **Stil:** sachlich, knapp, enzyklopädisch. Keine Werbesprache, keine Phrasen.
- **Zitate** kennzeichnest du mit `> "..."` und Quellen-Verweis (Wikilink auf die Summary-Page).
- **Maße/Daten:** SI-Einheiten, ISO-Datumsformat (`2026-04-11`), 24h-Zeitformat.

## 3. Page-Typen & Verzeichnis-Routing

| Typ | Verzeichnis | Wofür |
|---|---|---|
| **Topic** | `wiki/topics/` | Übergeordnetes Themengebiet (entspricht 1:1 einem KB-Verzeichnis aus `raw/kb/`) |
| **Entity** | `wiki/entities/` | Greifbare Dinge: Personen, Orte, Organisationen, Hersteller, Standards-Gremien |
| **Concept** | `wiki/concepts/` | Abstrakte Dinge: Protokolle, Begriffe, Theorien, Architekturen, Ereignisse |
| **Tool** | `wiki/tools/` | Software, Hardware, Produkte (z.B. Proxmox VE, CheckMK, Baramundi, Pfsense) |
| **Procedure** | `wiki/procedures/` | Anleitungen, Schritt-für-Schritt-Howtos, Konfigurationsrezepte |
| **Summary** | `wiki/summaries/` | Genau eine Quelle, ihre Kernaussagen, Bibliografie |

Wenn du unsicher bist (z.B. "Quantencomputer" — Konzept oder Entity?), wähle **Concept** und vermerke es im Page-Body.

**Routing-Hilfe für IT-Domäne:**

- **"IPv6"** → Concept (es ist ein Protokoll, kein Produkt). **Plus** eine Topic-Page `wiki/topics/IPv6.md`, die das ganze KB-Verzeichnis `raw/kb/IPv6/` als Eingangs-Übersicht abbildet.
- **"Pfsense"** → Tool (konkretes Produkt)
- **"BSI"** → Entity (Organisation)
- **"BSI IT-Grundschutz Kompendium"** → Summary (eine konkrete Quelle/Publikation)
- **"DHCP-Snooping aktivieren auf Cisco Catalyst"** → Procedure
- **"Datenraten Tabelle"** → Concept (Referenztabelle)
- **"Netzwerk Tier-Modell"** → Concept

**Über Topics:**
Pro KB-Verzeichnis (`raw/kb/<TopicName>/`) gibt es genau **eine Topic-Page** unter `wiki/topics/<TopicName>.md`. Diese Page ist die **Eingangstür** für das ganze Themengebiet. Sie bildet die Ablage-Struktur des KB-Verzeichnisses ab — also eine Tabelle aller Quellen mit Verweis auf die jeweilige Summary — plus eine gruppierte Übersicht aller abgeleiteten Pages (Entities, Concepts, Tools, Procedures). Damit gibt es im Wiki immer zwei Sichten auf denselben Inhalt: die **typenbasierte** (entities/concepts/...) und die **topicbasierte** (topics/IPv6/Proxmox/...).

## 4. Datei-Naming

- **Klartext mit Spaces** — `Andrej Karpathy.md`, nicht `andrej_karpathy.md`. Obsidian und Wikilinks lieben das.
- **Eindeutigkeit:** Bei Mehrdeutigkeit Klammer-Disambiguierung — `Mercury (Planet).md` vs. `Mercury (Element).md`.
- **Großschreibung:** wie im natürlichen Sprachgebrauch (Eigennamen groß).
- **Keine Datums-Präfixe** in Page-Namen — Datum gehört ins Frontmatter.

## 5. Page-Frontmatter (YAML)

Jede Wiki-Page beginnt mit YAML-Frontmatter:

```yaml
---
type: entity | concept | tool | procedure | summary
aliases: [Alternative Schreibweise, Englischer Name, Abkürzung]
tags: [tag1, tag2]
created: 2026-04-11
updated: 2026-04-11
sources:
  - "[[Summary-Page-1]]"
  - "[[Summary-Page-2]]"
---
```

**Optionale IT-Frontmatter-Felder** (nur setzen wenn sinnvoll):

```yaml
vendor: Microsoft         # Hersteller (für tools/)
product: Exchange Server  # Produktname
version: "2019"           # immer als String, nie als Zahl (sonst YAML-Cast)
os: [linux, windows]      # Betriebssystem(e), Liste
protocol: IPv6            # Netzwerkprotokoll-Bezug
standard: RFC4291         # Bezogener Standard / RFC / DIN
risk_level: high          # bei Security-Themen
```

**WICHTIG zum Frontmatter:**
Bei *mehreren* Sources MUSS die Block-Liste-Notation verwendet werden (Bindestrich + String in Anführungszeichen). Die Inline-Notation `sources: [[A]], [[B]]` ist **kein valides YAML** — Quartz und andere YAML-Parser werden daran scheitern, weil `[[…]]` als verschachtelte Listen interpretiert wird. Bei einer einzigen Source darf man die Inline-Form verwenden, aber konsistent ist die Block-Form besser.

- **`type`**: pflicht — entity, concept oder summary
- **`aliases`**: Liste alternativer Namen, hilft Obsidian beim Auto-Linking
- **`tags`**: thematisch (lowercase, snake-case), max. 5
- **`created`** / **`updated`**: ISO-Datum
- **`sources`**: Wikilinks auf alle Summary-Pages, die diese Page belegen

Bei **Summary-Pages** zusätzlich:
```yaml
source_file: ../raw/originaldatei.pdf
source_url: https://...
source_type: pdf | html | markdown | transcript | other
source_date: 2025-09-15        # Datum des Originals
ingested: 2026-04-11
```

## 6. Page-Struktur (Body)

### Entity-Page
```markdown
# Eigenname

> **Kurzdefinition** in einem Satz.

## Was es ist
Sachliche Beschreibung in 2–4 Absätzen.

## Verbindungen
- **Gehört zu**: [[Übergeordnete Entity]]
- **Verwandt mit**: [[Andere Entity]]
- **Verwendet**: [[Konzept]]

## Belege
- [[Summary 1]] — kurze Notiz, was diese Quelle dazu beiträgt
- [[Summary 2]] — ...

## Offene Fragen
- _was wir noch nicht wissen_
```

### Concept-Page
```markdown
# Begriff

> **Definition** in einem Satz.

## Idee
Was bedeutet der Begriff? Woher kommt er? Wie wird er verwendet?

## Abgrenzung
Verwandte Begriffe und ihre Unterschiede.

## Beispiele
Konkrete Anwendungen / Vorkommen.

## Belege
- [[Summary 1]] — ...

## Offene Fragen
- ...
```

### Topic-Page
```markdown
# <TopicName>

> **Was ist das?** Übergeordnete Eingangstür für das KB-Verzeichnis `raw/kb/<TopicName>/`.

## Worum geht es

Sachliche Beschreibung des Themengebiets in 2–4 Absätzen.

## Quellen in diesem Topic

(1:1 Spiegel der Files in `raw/kb/<TopicName>/`)

| Datei | Format | Summary |
|---|---|---|
| `IPv6 Routing.docx` | docx | [[IPv6 Routing per Router Advertisement (KB)]] |
| `ipv6_cheat_sheet.pdf` | pdf | [[IPv6 Cheat Sheet (Roesen)]] |
| ... | | |

## Konzepte aus diesem Topic

- [[Concept 1]]
- [[Concept 2]]

## Tools aus diesem Topic

- [[Tool 1]]
- [[Tool 2]]

## Procedures aus diesem Topic

- [[Procedure 1]]
- [[Procedure 2]]

## Entities aus diesem Topic

- [[Entity 1]]
- [[Entity 2]]
```

### Tool-Page
```markdown
# Produktname

> **Kurzbeschreibung** in einem Satz: was es ist, von wem, wofür.

## Was es ist
Sachliche Beschreibung in 2–4 Absätzen.

## Architektur / Komponenten
Aufbau, Hauptkomponenten, Technologie-Stack.

## Typische Anwendungsfälle
- ...

## Verwandte Tools / Alternativen
- [[Anderes Tool]] — Unterschied/Ähnlichkeit

## Anleitungen in diesem Wiki
- [[Procedure 1]]
- [[Procedure 2]]

## Belege
- [[Summary 1]]
```

### Procedure-Page
```markdown
# Anleitung: Was wird gemacht

> **Ziel**: Was diese Anleitung erreicht (1 Satz).
> **Anwendbar auf**: [[Tool X]] Version 1.2+, [[Tool Y]] mit Plugin Z

## Voraussetzungen
- ...
- ...

## Schritte

### 1. ...
```bash
befehl
```

### 2. ...

## Verifikation
Wie überprüfst du, dass es geklappt hat?
```bash
test-befehl
```

## Rollback
Wie machst du es rückgängig, falls etwas schiefgeht?

## Bekannte Stolpersteine
- ...

## Belege
- [[Summary 1]]
```

### Summary-Page
```markdown
# Titel der Quelle

## Kernaussagen
1. ...
2. ...
3. ...

## Inhalt im Detail
Strukturierte Wiedergabe — Abschnitte, Unter-Absätze.

## Wichtige Zitate
> "..." (S. 12)

## Berührte Wiki-Pages
- [[Entity 1]]
- [[Concept 2]]

## Bibliografie
- **Autor**: ...
- **Jahr**: ...
- **Verlag/Plattform**: ...
- **URL**: ...
- **Lokal**: `../raw/originaldatei.pdf`
```

## 7. Cross-References

- **Format**: `[[Page Name]]` (Obsidian-Wikilinks). Niemals Markdown-Links `[text](url)` für interne Verweise.
- **Wann?** Sobald eine Page eine andere existierende Page erwähnt oder eine plausible Verbindung besteht. Lieber zu viel als zu wenig — Obsidian zeigt Backlinks automatisch.
- **Aliase nutzen:** Wenn du `[[Andrej Karpathy|Karpathy]]` schreibst, ist der Link auf die Page, der angezeigte Text ist "Karpathy". So bleibt der Fließtext lesbar.
- **Vorwärts-Referenz auf nicht-existierende Pages** ist erlaubt — der Link wird in Obsidian rot angezeigt und ist eine Aufgabe für den nächsten Ingest oder Lint.

## 8. Workflows

### Ingest

Eine neue Quelle in `raw/` einarbeiten:

1. **Konvertierung prüfen.** Liegt für die Quelle bereits eine Markdown-Variante in `raw/converted/<...>.md`? Wenn ja, lies *die*. Wenn nein und das Original ist nicht-Markdown:
   - Office-Dateien → `scripts/convert-raw.sh <topic>` aufrufen, dann nochmal prüfen
   - Bei Bild-Inhalten/Scans → `OCR=1 scripts/convert-raw.sh <topic>` mit Tesseract
2. **Lesen.** Quelle vollständig lesen. Bei sehr großen PDFs (Bücher, Grundschutz-Kompendium): erst Inhaltsverzeichnis und Struktur erfassen, dann gezielt einzelne Kapitel.
3. **Diskussion.** Mit dem User die wichtigsten Punkte besprechen, falls Unklarheit oder mehrere mögliche Interpretationen.
4. **Summary anlegen.** Eine `wiki/summaries/<Titel>.md` mit dem Schema oben.
5. **Tool/Procedure/Entity/Concept ableiten.** Welche neuen Pages sind dazugekommen? Lege sie im richtigen Verzeichnis an. Bei IT-Inhalten besonders achten auf:
   - **Tool**: jede konkrete Software/Hardware → eigene Page (mit `vendor`, `product`, `version`)
   - **Procedure**: jedes Howto/jede Anleitung → eigene Page mit `## Voraussetzungen`, `## Schritte`, `## Verifikation`, `## Rollback`
   - **Concept**: Protokolle, Architekturen, Begriffe (IPv6, NAT, RAID, Tier-Modell, …)
6. **Bestehende Pages updaten.** Wenn die neue Quelle einer alten Page widerspricht, vermerke das **explizit** in beiden Pages (Abschnitt `## Widersprüche`). Bei IT-Inhalten besonders: Versionsangaben prüfen — eine Anleitung für Exchange 2013 ist nicht automatisch falsch, aber sie ist nicht für 2019.
7. **`index.md` aktualisieren.** Neue Pages einsortieren.
8. **`log.md` ergänzen.** Eintrag mit Format `## [YYYY-MM-DD HH:MM] ingest | <Quellen-Titel>`.
9. **Commit.** Git-Commit mit Message "ingest: <Quellen-Titel>".

**Faustregel:** Eine sinnvolle Ingest-Operation berührt 5–15 Pages. Bei IT-Quellen können es auch mal 20+ sein (eine Anleitung zur Exchange-Migration berührt: das Tool, mehrere Procedures, mehrere Concepts wie LDAP/AD/SMTP, und mehrere Entities wie Microsoft/RFC-Standards).

**Quellen-Pfade:**
- Originale liegen in `raw/kb/<Topic>/<Datei>.{docx,pdf,xlsx,...}`
- Markdown-Konvertierungen liegen parallel in `raw/converted/<Topic>/<Datei>.md`
- Beim Belegen (`source_file:`) auf das **Original** verweisen, nicht auf die Konvertierung. Die Konvertierung ist nur die Lesefassung.

### Query

Eine Frage gegen das Wiki beantworten:

1. **`index.md` lesen.** Erste Orientierung: welche Pages könnten relevant sein?
2. **Optional `qmd search "..."`** — Volltextsuche, wenn der Index nicht ausreicht.
3. **Relevante Pages laden.** 3–10 Pages, kein blindes Schrotflinten-Laden.
4. **Antworten mit Zitaten.** Jede Aussage mit `[[Page]]`-Verweis. Wenn etwas nicht im Wiki belegt ist, sag es ehrlich.
5. **Rückspeisung prüfen.** Ist die Antwort eine *neue* Erkenntnis? Eine Vergleichstabelle? Eine Verbindung, die noch keiner Page lebt? Dann **als neue Page in `wiki/` filen** — Karpathys Hinweis: gute Antworten sollen nicht im Chat verschwinden.
6. **`log.md` ergänzen** — `## [YYYY-MM-DD HH:MM] query | <Frage>`.

### Lint

Periodischer Health-Check:

1. **Widersprüche** zwischen Pages? (Wenn Page A sagt "1942" und Page B sagt "1943", flag es.)
2. **Orphan-Pages** — Pages ohne Inbound-Links. Entweder löschen oder sinnvoll verlinken.
3. **Stub-Pages** — Pages mit < 3 Sätzen Inhalt. Wenn die Quelle mehr hergibt: erweitern; wenn nicht: in eine andere Page einfügen.
4. **Erwähnte aber nicht angelegte Konzepte** — `grep -oP '\[\[[^\]]+\]\]' wiki/**/*.md`, dann prüfen ob jede Ziel-Page existiert. Rote Links anlegen oder aus dem Text entfernen.
5. **Stale claims** — Aussagen, die durch neuere Quellen überholt sein könnten? Kandidat-Liste produzieren.
6. **Lint-Report** in `docs/lint_<datum>.md` mit Befunden + Korrekturen.
7. **`log.md` ergänzen.**

## 9. Tooling

- **Suche:** Falls qmd verfügbar (`qmd search "..."` oder via MCP-Server). Sonst `rg "..." wiki/`.
- **Wikilink-Extraktion:** `grep -hoP '\[\[[^\]]+\]\]' wiki/**/*.md | sort -u`
- **Backlink-Suche:** `rg -l '\[\[Page Name\]\]' wiki/`
- **Konvertierungs-Helper:** `scripts/convert-raw.sh [Topic ...]` — wandelt alle nicht-Markdown-Quellen aus `raw/kb/` nach `raw/converted/`. Idempotent.
- **PDF→Text:** `pdftotext -layout raw/kb/datei.pdf -` (für Ad-hoc-Lektüre)
- **PDF mit OCR:** `OCR=1 scripts/convert-raw.sh <topic>` für gescannte Dokumente
- **Office:** `pandoc raw/kb/datei.docx -o /tmp/datei.md`, oder über `convert-raw.sh`
- **HTML→Markdown:** `pandoc raw/kb/datei.html -t markdown -o /tmp/datei.md`

## 10. Was du NICHT tust

- **`raw/` editieren.** Niemals. Quellen sind immutable.
- **Stille Löschungen.** Wenn du eine Page löschst, dokumentiere im `log.md` warum.
- **Übersetzung verfälschen.** Lieber Original-Zitat in Originalsprache + deutsche Übersetzung.
- **Vorhandene Cross-References löschen** ohne Grund. Wenn ein Link ins Leere geht, lege die Ziel-Page an, statt den Link zu killen.
- **Halluzinieren.** Wenn die Quellen es nicht hergeben, schreib es nicht. "Unbelegt" ist eine erlaubte Aussage.

## 11. Erfolgskriterien für ein gesundes Wiki

- **Vernetzung:** durchschnittlich ≥ 3 Inbound-Links pro Wiki-Page (außer Stubs)
- **Belegtheit:** jede Aussage in Entity/Concept-Pages durch mindestens eine `sources:`-Eintrag belegt
- **Aktualität:** `log.md` ist nicht älter als der letzte Commit
- **Konsistenz:** keine Widersprüche, oder, wenn welche existieren, sind sie als solche markiert

## 12. Benchmark-Framework

Für das Testen neuer Modelle existiert ein reproduzierbares Benchmark-Framework unter `benchmark/`. Dokumentation: `benchmark/README.md`. Kurzfassung:

```bash
# Lokales Modell testen
benchmark/benchmark.sh <tag> benchmark/configs/<tag>.env <strict|free>

# Claude-Familie testen
benchmark/claude-benchmark.sh <tag> <model-id>

# GPT via Codex testen
benchmark/codex-benchmark.sh
```

Zentrale Erkenntnis: Modelle ≤8B brauchen STRICT-Schema, Modelle ≥10B und Frontier-Modelle brauchen FREE-Schema oder Agent-Modus.

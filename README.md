# LLM Wiki — Codebase

Werkzeuge für ein LLM-gepflegtes Markdown-Wiki nach dem [LLM-Wiki-Pattern von Andrej Karpathy](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f): Quellen konvertieren, Wiki-Seiten erzeugen und die Qualität verschiedener Modelle vergleichen. Obsidian dient als lokale Oberfläche, Quartz als Web-Visualisierung.

Dieses Repository enthält ausschließlich Code, Konfiguration und technische Anleitung. Quellen (`raw/`), Wiki-Seiten (`wiki/`), der generierte Katalog und Verlauf (`index.md`, `log.md`), Benchmark-Ergebnisse, Berichte, Screenshots und lokale Zugangsdaten sind nicht enthalten. Auch die bisherige lokale Git-Historie wurde nicht übernommen, da sie Wiki-Inhalte enthält.

## Aufbau

- `CLAUDE.md`: Schema und Arbeitsabläufe für den Wiki-Agenten (Ingest, Query, Lint).
- `scripts/`: Quellenkonvertierung, Bildbeschreibungen und Folienexport.
- `benchmark/`: Runner für Claude, Codex und OpenAI-kompatible Modell-Endpunkte; Versionen der Ingest- und Prüfverfahren einschließlich Groundedness, Zitationen, Widersprüchen, Re-Ranking und HyDE.
- `benchmark/configs/model.env.example`: Beispiel ohne Zugangsdaten.
- `.obsidian/`: grundlegende Vault-Einstellungen ohne Sitzungsdaten.
- `quartz/`: eingebundener Quartz-Quellcode mit der lokalen LLM-Wiki-Konfiguration.

## Lokale Einrichtung

Die Skripte stammen aus einer Serverinstallation unter `/opt/llm-wiki`. Teilweise enthalten sie feste Pfade, interne Dienst-Adressen und SSH-Ziele. Vor der Verwendung die betreffenden Skripte und `quartz/quartz.config.ts` an die eigene Umgebung anpassen; vorhandene Umgebungsvariablen sind direkt im jeweiligen Skript dokumentiert.

Benötigt werden je nach Werkzeug Bash, Python 3 (für die v8-Prüfungen zusätzlich `numpy`), Konvertierungsprogramme wie Pandoc und Poppler sowie ein erreichbarer Modell-Dienst oder eine eingerichtete Claude-/Codex-CLI. Die v8-Prüfungen setzen zusätzlich die im Code beschriebenen NLI-/Cross-Encoder-Dienste voraus. Quartz benötigt Node.js >= 22 und npm >= 10.9.2.

Quellen und Wiki-Verzeichnisse lokal anlegen:

```bash
mkdir -p raw/kb wiki/{topics,entities,concepts,tools,procedures,summaries}
cp benchmark/configs/model.env.example benchmark/configs/model.env
```

Die lokale Modellkonfiguration ergänzen und eigene Quellen bereitstellen. Die Benchmark-Runner erwarten derzeit den in [benchmark/README.md](benchmark/README.md) beschriebenen IPv6-Testkorpus unter `raw/converted-v5/`; dieser ist ausdrücklich nicht enthalten. Verweise in der Benchmark-Anleitung auf Auswertungsberichte beziehen sich auf die ursprüngliche lokale Installation, deren Berichte hier nicht veröffentlicht werden.

Für Obsidian den Projektordner als Vault öffnen. Für Quartz Abhängigkeiten installieren und anschließend ein lokal bereitgestelltes Inhaltsverzeichnis bauen:

```bash
cd quartz
npm ci
npx quartz build --directory ../wiki --serve
```

Vor einer Veröffentlichung `baseUrl` in `quartz/quartz.config.ts` auf die gewünschte Domain setzen. Generierte Webseiten bleiben durch `.gitignore` vom Code-Repository ausgeschlossen.

## Quartz-Herkunft

Quartz wurde aus [jackyzha0/quartz](https://github.com/jackyzha0/quartz) übernommen, Stand `59b58076016c5e5f09f16ab0395fed02e2ebb72e` (Version 4.5.2), einschließlich lokaler Änderungen an `quartz.config.ts`. Der Quellcode liegt direkt im Repository, sodass kein separat zu initialisierendes Submodul nötig ist. Für diesen Bestandteil gilt die mitgelieferte [MIT-Lizenz](quartz/LICENSE.txt).

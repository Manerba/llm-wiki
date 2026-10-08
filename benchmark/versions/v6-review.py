#!/usr/bin/env python3
"""
v6-review.py — Self-Review-Pass über ein bestehendes Wiki.

Das LLM liest jede Page im Vault und prüft:
1. Klassifikation korrekt? (entity/concept/tool/procedure/summary/topic)
2. Halluzination? (Inhalt nicht aus Quellen ableitbar?)
3. Wikilinks fehlen? (existierende Pages im Text erwähnt aber nicht verlinkt?)
4. Stub? (weniger als 3 Sätze Body?)
5. Frontmatter vollständig?

Pro Page wird ein Review-Ergebnis erzeugt. Bei Problemen wird die Page
korrigiert (neue Version geschrieben). Ein Review-Report wird generiert.

Usage:
    VAULT=/opt/llm-wiki-bench-v5-qwen3-8b-strict \
    ENV_FILE=/root/.config/qwen-ingest.env \
    python3 v6-review.py

    # Opus-Review (Claude Code macht es direkt, kein API-Call nötig)
    # → wird manuell in der Session ausgeführt
"""
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

VAULT = Path(os.environ.get("VAULT", "/opt/llm-wiki-bench-v5-qwen3-8b-strict"))
ENV_FILE = Path(os.environ.get("ENV_FILE", "/root/.config/qwen-ingest.env"))
RESULTS_DIR = Path("/opt/llm-wiki/benchmark/results")

REVIEW_PROMPT = """\
Du bist ein Wiki-Qualitätsprüfer. Prüfe die folgende Wiki-Page auf Fehler.

PRÜFKRITERIEN:
1. KLASSIFIKATION: Ist der Page-Typ korrekt?
   - entity = konkrete Dinge (Personen, Organisationen, Hersteller)
   - concept = abstrakte Dinge (Protokolle, Standards, RFCs, Begriffe, Mechanismen)
   - tool = konkrete Software/Hardware (ip6tables, radvd, FRITZ!Box)
   - procedure = Schritt-für-Schritt-Anleitung
   - summary = Zusammenfassung einer Quelle
   - topic = Themenübersicht
   HÄUFIGE FEHLER: RFCs als entity (sollte concept), Tunnel/Adresse/Header als entity (sollte concept)

2. HALLUZINATION: Steht der Inhalt WIRKLICH in den verlinkten Quellen, oder ist es erfundenes Modell-Wissen?

3. WIKILINKS: Werden existierende Wiki-Pages im Text erwähnt, aber NICHT als [[Wikilink]] verlinkt?

4. STUB: Hat die Page weniger als 3 Sätze Body-Text?

5. FRONTMATTER: Sind type, aliases, tags, created, updated, sources vorhanden?

EXISTIERENDE PAGES (für Wikilink-Prüfung):
{existing_pages}

PAGE ZU PRÜFEN ({page_path}):
```
{page_content}
```

ANTWORTE NUR im folgenden JSON-Format, NICHTS anderes:
{{
  "classification_ok": true/false,
  "classification_fix": "concepts" oder null,
  "hallucination": true/false,
  "hallucination_detail": "..." oder null,
  "missing_wikilinks": ["Page A", "Page B"] oder [],
  "is_stub": true/false,
  "frontmatter_ok": true/false,
  "frontmatter_issues": ["..."] oder [],
  "overall_ok": true/false,
  "suggested_body": "korrigierter Body (NUR wenn Korrekturen nötig, sonst null)"
}}
"""


def load_env(path):
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def list_pages(vault):
    pages = {}
    for p in (vault / "wiki").rglob("*.md"):
        rel = str(p.relative_to(vault / "wiki"))
        if rel.startswith(("v1", "v2", "v3")):
            continue
        pages[p.stem] = p
    return pages


def call_ollama(env, prompt, max_tokens=4000, num_ctx=16384):
    api_type = env.get("QWEN_API_TYPE", "ollama").lower()
    base = env["QWEN_BASE_URL"].rstrip("/")
    headers = {"Content-Type": "application/json"}

    if api_type == "openai":
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        url = f"{base}/chat/completions"
        api_key = env.get("QWEN_API_KEY", "")
        if api_key and api_key != "not-needed":
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": env["QWEN_MODEL"],
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.1,
            "stream": False,
        }
    else:
        if base.endswith("/v1"):
            base = base[:-3]
        url = f"{base}/api/chat"
        payload = {
            "model": env["QWEN_MODEL"],
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"num_ctx": num_ctx, "num_predict": max_tokens, "temperature": 0.1},
        }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as resp:
        body = resp.read().decode("utf-8")
    dt = time.time() - t0
    obj = json.loads(body)
    if api_type == "openai":
        content = obj["choices"][0]["message"].get("content", "") or ""
    else:
        content = obj.get("message", {}).get("content", "") or ""
    return content, dt


def parse_review(text):
    """Extrahiert JSON aus der Antwort (auch wenn Markdown-Codeblöcke drumherum sind)."""
    text = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    text = re.sub(r"\n?```\s*$", "", text.strip())
    # Finde das erste { ... } Objekt
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


def add_missing_wikilinks(body, missing):
    """Fügt fehlende Wikilinks in den Body ein (erstes Vorkommen)."""
    for page_name in missing:
        pattern = re.compile(r'(?<!\[)(?<!\w)(' + re.escape(page_name) + r')(?!\w)(?!\])', re.IGNORECASE)
        body = pattern.sub(f"[[{page_name}]]", body, count=1)
    return body


def fix_classification(page_path, new_type):
    """Verschiebt eine Page in das korrekte Verzeichnis."""
    p = Path(page_path)
    parts = list(p.parts)
    type_dirs = {"concepts", "entities", "tools", "procedures", "summaries", "topics"}
    for i, part in enumerate(parts):
        if part in type_dirs:
            parts[i] = new_type
            break
    return Path(*parts)


def main():
    env = load_env(ENV_FILE)
    pages = list_pages(VAULT)
    existing_names = sorted(pages.keys())
    existing_str = ", ".join(f"[[{n}]]" for n in existing_names[:100])

    print(f"v6 Self-Review: {VAULT}")
    print(f"Modell: {env['QWEN_MODEL']}")
    print(f"Pages zu reviewen: {len(pages)}")

    results = []
    fixes_applied = 0
    ts = datetime.now().strftime("%Y-%m-%d_%H%M")

    for i, (name, path) in enumerate(sorted(pages.items())):
        rel = str(path.relative_to(VAULT))
        content = path.read_text()
        print(f"\n[{i+1}/{len(pages)}] {rel}")

        prompt = REVIEW_PROMPT.format(
            existing_pages=existing_str,
            page_path=rel,
            page_content=content[:6000],
        )

        raw, dt = call_ollama(env, prompt, max_tokens=3000)
        review = parse_review(raw)

        if not review:
            print(f"  ⚠ Review-JSON nicht parsebar ({dt:.1f}s)")
            results.append({"page": rel, "status": "parse_error", "duration": dt})
            continue

        ok = review.get("overall_ok", True)
        issues = []
        if not review.get("classification_ok", True):
            issues.append(f"K→{review.get('classification_fix','?')}")
        if review.get("hallucination"):
            issues.append(f"H:{review.get('hallucination_detail','')[:40]}")
        if review.get("missing_wikilinks"):
            issues.append(f"WL+{len(review['missing_wikilinks'])}")
        if review.get("is_stub"):
            issues.append("STUB")
        if not review.get("frontmatter_ok", True):
            issues.append("FM")

        status = "OK" if ok else ", ".join(issues)
        print(f"  {'✓' if ok else '✗'} {status} ({dt:.1f}s)")

        # Korrekturen anwenden
        modified = False
        if review.get("missing_wikilinks") and not ok:
            content = add_missing_wikilinks(content, review["missing_wikilinks"])
            modified = True

        if not review.get("classification_ok") and review.get("classification_fix"):
            new_path = fix_classification(path, review["classification_fix"])
            if new_path != path:
                new_abs = VAULT / str(new_path.relative_to(VAULT) if new_path.is_relative_to(VAULT) else new_path)
                # Eigentlich müsste man die Datei verschieben — hier nur loggen
                issues.append(f"MOVE→{review['classification_fix']}")

        if modified:
            path.write_text(content)
            fixes_applied += 1

        results.append({
            "page": rel,
            "status": status,
            "ok": ok,
            "duration": dt,
            "issues": issues,
            "missing_wl": len(review.get("missing_wikilinks", [])),
        })

    # Report schreiben
    report_path = RESULTS_DIR / f"{env['QWEN_MODEL'].replace(':','-').replace('/','_')}-v6-review.tsv"
    with open(report_path, "w") as f:
        f.write("page\tok\tstatus\tduration_s\tmissing_wl\n")
        for r in results:
            f.write(f"{r['page']}\t{r.get('ok','?')}\t{r['status']}\t{r.get('duration',0):.1f}\t{r.get('missing_wl',0)}\n")

    total = len(results)
    ok_count = sum(1 for r in results if r.get("ok"))
    print(f"\n{'='*50}")
    print(f"v6 Review fertig: {ok_count}/{total} OK ({ok_count/total*100:.1f}%)")
    print(f"Fixes angewandt: {fixes_applied}")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()

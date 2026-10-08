#!/usr/bin/env python3
"""Parst ===PAGE:=== Marker aus stdin und schreibt Pages in den Vault."""
import sys, re, os

vault = sys.argv[1] if len(sys.argv) > 1 else "/opt/llm-wiki-bench-sonnet"
text = sys.stdin.read()

marker = re.compile(r'^===\s*PAGE:\s*(.+?\.md)\s*===\s*$', re.MULTILINE)
matches = list(marker.finditer(text))
written = 0

PLURAL = {
    "wiki/concept/": "wiki/concepts/",
    "wiki/entity/": "wiki/entities/",
    "wiki/summary/": "wiki/summaries/",
    "wiki/tool/": "wiki/tools/",
    "wiki/procedure/": "wiki/procedures/",
    "wiki/topic/": "wiki/topics/",
    # Non-standard dirs that models might use
    "wiki/netzwerk/": "wiki/concepts/",
    "wiki/standard/": "wiki/concepts/",
    "wiki/protocol/": "wiki/concepts/",
    "wiki/reference/": "wiki/concepts/",
}

for i, m in enumerate(matches):
    path = m.group(1).strip()
    start = m.end()
    end = matches[i+1].start() if i+1 < len(matches) else len(text)
    body = text[start:end].strip()

    # Strip markdown codeblock wrappers
    body = re.sub(r'^```(?:markdown|yaml)?\s*\n', '', body)
    body = re.sub(r'\n```\s*$', '', body)
    body = body.strip()

    if not body:
        continue

    # Fix directory names
    for singular, plural in PLURAL.items():
        if path.startswith(singular):
            path = plural + path[len(singular):]
            break

    if not path.startswith("wiki/"):
        continue

    full = os.path.join(vault, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, 'w') as f:
        f.write(body)
    written += 1
    print(f"  ✓ {path}")

print(f"\n  {written} Pages geschrieben")

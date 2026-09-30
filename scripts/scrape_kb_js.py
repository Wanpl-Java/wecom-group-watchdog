#!/usr/bin/env python3
"""One-off: scrape JumpServer KB article title/url pairs."""
from __future__ import annotations

import re
import httpx

URLS = [
    "https://kb.fit2cloud.com/categories/jumpserver",
    "https://kb.fit2cloud.com/categories/jumpserver?page=2",
    "https://kb.fit2cloud.com/categories/jumpserver?page=3",
    "https://kb.fit2cloud.com/categories/jumpserver?page=4",
    "https://kb.fit2cloud.com/categories/jumpserver?page=5",
]

seen: set[str] = set()
articles: list[tuple[str, str]] = []

for u in URLS:
    try:
        r = httpx.get(u, timeout=30, follow_redirects=True)
        html = r.text
    except Exception as e:  # noqa: BLE001
        print("fail", u, e)
        continue
    for m in re.finditer(r'href="(\?p=[^"]+)"[^>]*>\s*([^<]{4,160})', html):
        href, title = m.group(1), re.sub(r"\s+", " ", m.group(2)).strip()
        full = "https://kb.fit2cloud.com/" + href
        if full in seen:
            continue
        seen.add(full)
        articles.append((title, full))
    # also absolute /?p=
    for m in re.finditer(r'href="(https://kb\.fit2cloud\.com/\?p=[^"]+)"[^>]*>\s*([^<]{4,160})', html):
        full, title = m.group(1), re.sub(r"\s+", " ", m.group(2)).strip()
        if full in seen:
            continue
        seen.add(full)
        articles.append((title, full))
    print(u, "status", r.status_code, "arts", len(articles))

print("---")
for t, f in articles:
    print(f"{t}\t{f}")

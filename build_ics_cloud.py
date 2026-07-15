#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cloud-Variante (GitHub Actions): holt die Seiten selbst per HTTP (requests),
wandelt sie in ein web_fetch-aehnliches Markdown und nutzt danach die
getesteten Parser aus stpauli_ics.py, um die .ics zu bauen.

Laeuft in GitHub Actions (voller Internetzugang). Erzeugt st_pauli_tickets.ics.
"""
from __future__ import annotations
import os
import shutil
import sys
import requests
from markdownify import markdownify as to_md

import stpauli_ics as core
import extract_urls as ex

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}
INPUT_DIR = "inputs"
OUT = "st_pauli_tickets.ics"


def fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def title_of(html: str) -> str:
    import re
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def as_webfetch(url: str, html: str) -> str:
    """Baut Text im Format der web_fetch-Ausgabe: Titelzeile, URL-Zeile(n),
    dann Markdown-Koerper. So funktionieren die vorhandenen Parser unveraendert."""
    body = to_md(html, heading_style="ATX", strip=["script", "style"])
    return f"{title_of(html)}\n{url}\n→ {url}\nContent-Type: text/html\n\n{body}"


def save(idx: int, prefix: str, url: str, text: str):
    path = os.path.join(INPUT_DIR, f"{prefix}_{idx:02d}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def main():
    if os.path.isdir(INPUT_DIR):
        shutil.rmtree(INPUT_DIR)
    os.makedirs(INPUT_DIR, exist_ok=True)

    # 1) Uebersichtsseiten (Verein + Fanladen)
    saved_listing_texts = []
    for i, url in enumerate(ex.LISTINGS):
        try:
            text = as_webfetch(url, fetch(url))
        except Exception as e:
            print(f"WARN listing {url}: {e}", file=sys.stderr)
            continue
        save(i, "list", url, text)
        saved_listing_texts.append(text)
        print(f"[listing] {url}  len={len(text)}  "
              f"begegnungen={'## Nächste Begegnungen' in text}  "
              f"ticket-infos={'ticket-infos' in text}  "
              f"spieltagsinfos={'Spieltagsinfos' in text}")

    # 2) Detail-URLs aus den Uebersichtsseiten ermitteln
    detail = set()
    for text in saved_listing_texts:
        if "## Nächste Begegnungen" in text:
            for m in ex.RE_SPIELTAGS.finditer(text):
                detail.add(m.group(1).split("?")[0])
            for m in ex.RE_NEWS.finditer(text):
                detail.add(m.group(0).split("?")[0])
        for m in ex.RE_FL_POST.finditer(text):
            u = m.group(0).split("?")[0]
            slug = u.rstrip("/").rsplit("/", 1)[-1].lower()
            if "spiel" in slug or "pokal" in slug:
                detail.add(u)

    print(f"[detail] {len(detail)} Detail-URLs gefunden:")
    for u in sorted(detail):
        print(f"    - {u}")

    # 3) Detailseiten holen
    for i, url in enumerate(sorted(detail)):
        try:
            text = as_webfetch(url, fetch(url))
        except Exception as e:
            print(f"WARN detail {url}: {e}", file=sys.stderr)
            continue
        save(i, "article", url, text)

    # 4) .ics bauen (getestete Parser)
    summary = core.run(INPUT_DIR, OUT)
    print(f"Spiele: {summary['matches']} | Artikel: {summary['articles_parsed']}")
    for e in summary["events"]:
        vt = "; ".join(f"{v['art']} {v['datum']}" for v in e["verkaufstermine"]) or "—"
        print(f"  • {e['datum']}  {e['spiel']}  -> {vt}")


if __name__ == "__main__":
    main()

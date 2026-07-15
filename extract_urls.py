#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hilfsskript fuer den woechentlichen Ablauf.

  --listings   gibt die Start-/Uebersichtsseiten aus, die zuerst per web_fetch
               geholt werden (Verein: Heim + Auswaerts; Fanladen: Start +
               "naechste Auswaertsfahrten").
  --from DIR   liest die bereits gespeicherten Uebersichtsseiten in DIR und gibt
               die darin verlinkten Detail-Seiten aus (Verein: Ticket-/Spieltags-
               Artikel; Fanladen: datierte Beitraege zu Spielen), die anschliessend
               ebenfalls per web_fetch geholt werden.
"""
import argparse
import glob
import os
import re

LISTINGS = [
    "https://www.fcstpauli.com/fu%C3%9Fball/tickets/heimspiele",
    "https://www.fcstpauli.com/fu%C3%9Fball/tickets/auswaertsspiele",
    "https://www.stpauli-fanladen.de/",
    "https://www.stpauli-fanladen.de/tickets/die-naechsten-auswaertsfahrten/",
    "https://www.stpauli-fanladen.de/news/",
]

# Verein: Ticket-/Spieltags-Artikel
RE_NEWS = re.compile(
    r"https?://www\.fcstpauli\.com/news/"
    r"(?:ticket-infos[^)\s]*|[a-z0-9\-]*spieltagsinfos[^)\s]*)", re.I)
RE_SPIELTAGS = re.compile(r"\[Spieltagsinfos\]\((https?://[^)]+)\)")
# Fanladen: datierte Beitraege /JJJJ/MM/TT/slug/
RE_FL_POST = re.compile(
    r"https?://www\.stpauli-fanladen\.de/20\d\d/\d{2}/\d{2}/[a-z0-9\-]+/?", re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listings", action="store_true")
    ap.add_argument("--from", dest="src_dir")
    args = ap.parse_args()

    if args.listings:
        for u in LISTINGS:
            print(u)
        return

    if args.src_dir:
        urls = set()
        for fp in glob.glob(os.path.join(args.src_dir, "*")):
            if os.path.isdir(fp):
                continue
            try:
                txt = open(fp, encoding="utf-8").read()
            except Exception:
                continue
            # Verein-Artikel nur aus den Verein-Listings
            if "## Nächste Begegnungen" in txt:
                for m in RE_SPIELTAGS.finditer(txt):
                    urls.add(m.group(1).split("?")[0])
                for m in RE_NEWS.finditer(txt):
                    urls.add(m.group(0).split("?")[0])
            # Fanladen-Beitraege: nur Spiel-/Pokal-bezogene Slugs
            for m in RE_FL_POST.finditer(txt):
                u = m.group(0).split("?")[0]
                slug = u.rstrip("/").rsplit("/", 1)[-1].lower()
                if "spiel" in slug or "pokal" in slug:
                    urls.add(u)
        for u in sorted(urls):
            print(u)


if __name__ == "__main__":
    main()

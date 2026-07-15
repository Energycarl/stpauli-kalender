#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FC St. Pauli – Ticket-Kalender
==============================

Erzeugt eine .ics-Datei mit
  * einem Termin je Spiel (Heim + Auswaerts, inkl. Test-/Pokalspiele)
  * separaten Erinnerungen zu Verkaufs-/Anmelde-Terminen
    (Mitglieder-Vorverkauf, freier Verkauf, Auswaerts-Verkaufsphasen,
     ADK-Reservierung, Zweitmarkt) und zu Fristen.

WICHTIG zur Architektur:
  Die Cowork-/Sandbox-Umgebung darf fcstpauli.com NICHT direkt aufrufen.
  Das Abrufen der Seiten uebernimmt der Agent (web_fetch) und speichert die
  Ausgaben als Textdateien in einem Eingabe-Ordner. Dieses Skript liest nur
  diese lokalen Textdateien (offline) und baut daraus die .ics.

Aufruf:
  python3 stpauli_ics.py --in inputs/ --out st_pauli_tickets.ics

Die abzurufenden Quell-URLs stehen unten in SOURCES.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, date, timedelta, timezone
from urllib.parse import unquote

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Berlin")
except Exception:  # pragma: no cover
    TZ = None  # Fallback wird unten behandelt

from icalendar import Calendar, Event, Alarm

# --------------------------------------------------------------------------
# Quell-URLs (die der woechentliche Task per web_fetch abruft)
# --------------------------------------------------------------------------
SOURCES = {
    "listings": [
        "https://www.fcstpauli.com/fu%C3%9Fball/tickets/heimspiele",
        "https://www.fcstpauli.com/fu%C3%9Fball/tickets/auswaertsspiele",
    ],
    # Artikel-URLs werden zur Laufzeit aus den Listings gelesen
    # (die "Spieltagsinfos"-Links jedes Spiels).
}

WEEKDAYS = {"Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"}

# --------------------------------------------------------------------------
# Regexe
# --------------------------------------------------------------------------
RE_SOURCE_URL = re.compile(r"https?://www\.fcstpauli\.com/\S+")
RE_LIST_DATE = re.compile(
    r"(Mo|Di|Mi|Do|Fr|Sa|So),\s*(\d{2})\.(\d{2})\.(\d{4})\s*\|\s*(\d{2}):(\d{2})"
)
RE_TEAM = re.compile(r"!\[[^\]]*\]\([^)]*\)\s+(.+?)\s*$")
RE_INFOS = re.compile(r"\[Spieltagsinfos\]\((https?://[^)]+)\)")
RE_SHOP = re.compile(r"\[(?:Ticket-Onlineshop|Tickets)\]\((https?://[^)]+)\)")
RE_STATUS = re.compile(r"\[(?:Ticket-Onlineshop|Tickets)\]\([^)]+\)\s+(.+?)\s*$")

# Datums-Token in Artikeln:  (21.7., 15 Uhr) | (21.7., ab 15 Uhr) | (13.7.) | (9.7.)
RE_ART_DATE_PAREN = re.compile(
    r"\((\d{1,2})\.(\d{1,2})\.(?:\s*,\s*(?:ab\s*)?(\d{1,2})(?::(\d{2}))?\s*Uhr)?\)"
)
# ... den 11.8., 15 Uhr
RE_ART_DATE_DEN = re.compile(
    r"den\s+(\d{1,2})\.(\d{1,2})\.\s*,?\s*(\d{1,2})(?::(\d{2}))?\s*Uhr"
)
# Veroeffentlichungsdatum oben im Artikel: 14.07.26
RE_PUBDATE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{2})\b")
RE_H1 = re.compile(r"(?m)^#\s+(.+?)\s*$")

# --- Fanladen (WordPress) ---
# Titel: "... bei Rot-Weiß Essen am 23.08. um 18 Uhr" / "... in Emden am 04.07. um 18 Uhr"
RE_FL_TITLE_DATE = re.compile(
    r"\bam\s+(\d{1,2})\.(\d{1,2})\.(?:\s*um\s*(\d{1,2})(?::(\d{2}))?\s*Uhr)?", re.I)
RE_FL_OPP = re.compile(r"\b(?:bei|in|gegen)\s+(.+?)\s+am\s+\d{1,2}\.\d{1,2}\.", re.I)
# Datum im Beitragspfad: /2026/07/07/
RE_FL_URLDATE = re.compile(r"/(20\d\d)/(\d{2})/(\d{2})/")
# Bestellfrist-Beginn: "Bestellfrist beginnt am 10.07."
RE_FL_START = re.compile(
    r"(?:Bestellfrist|Bestellung|Bestellungen|Anfragen)[^.]{0,40}?"
    r"(?:beginnt|startet|ab)\s*(?:am\s*)?(\d{1,2})\.(\d{1,2})\.", re.I)
# Bestellschluss: "läuft bis einschließlich 19.07." / "bis einschließlich 19.07." /
#                 "Bestellschluss ... 19.07." / "Meldeschluss ... 19.07."
RE_FL_END = re.compile(
    r"(?:bis(?:\s+einschließlich)?|Bestellschluss|Meldeschluss|Anmeldeschluss)"
    r"[^.\d]{0,25}?(\d{1,2})\.(\d{1,2})\.", re.I)


# --------------------------------------------------------------------------
# Helfer
# --------------------------------------------------------------------------
def slug_of(url: str) -> str:
    url = unquote(url or "")
    url = url.split("?")[0].rstrip("/")
    return url.rsplit("/", 1)[-1].lower()


def is_stpauli(name: str) -> bool:
    n = name.lower()
    return "st. pauli" in n or "st.pauli" in n or "fc st pauli" in n


def clean(txt: str) -> str:
    return re.sub(r"\s+", " ", (txt or "")).strip()


def to_utc(dt_local: datetime) -> datetime:
    if TZ is not None:
        return dt_local.replace(tzinfo=TZ).astimezone(timezone.utc)
    # grober Fallback: DE Sommerzeit ~ Ende Maerz–Ende Okt = UTC+2, sonst +1
    off = 2 if 3 < dt_local.month < 11 else 1
    return (dt_local - timedelta(hours=off)).replace(tzinfo=timezone.utc)


def source_url_of(text: str) -> str:
    head = "\n".join(text.splitlines()[:6])
    m = RE_SOURCE_URL.search(head)
    return m.group(0) if m else ""


def section(text: str, start_marker: str, stop_markers) -> str:
    i = text.find(start_marker)
    if i < 0:
        return ""
    rest = text[i + len(start_marker):]
    cut = len(rest)
    for sm in stop_markers:
        j = rest.find(sm)
        if 0 <= j < cut:
            cut = j
    return rest[:cut]


# --------------------------------------------------------------------------
# Listing-Parser  ->  Spiele
# --------------------------------------------------------------------------
def parse_listing(text: str) -> list[dict]:
    src = source_url_of(text)
    sec = section(
        text,
        "## Nächste Begegnungen",
        ["\n## ", "[Zum Spielplan]", "\n#### "],
    )
    if not sec:
        return []
    blocks = re.split(r"(?m)^- ", sec)[1:]
    out = []
    for block in blocks:
        mdate = RE_LIST_DATE.search(block)
        if not mdate:
            continue
        wd, dd, mm, yy, hh, mi = mdate.groups()
        try:
            dt = datetime(int(yy), int(mm), int(dd), int(hh), int(mi))
        except ValueError:
            continue
        lines = block.splitlines()
        label = clean(lines[0]) if lines else ""
        # Venue = erste nicht-leere Zeile nach der Datumszeile
        venue = ""
        date_idx = next(
            (k for k, ln in enumerate(lines) if RE_LIST_DATE.search(ln)), 0
        )
        for ln in lines[date_idx + 1:]:
            if ln.strip():
                venue = clean(ln)
                break
        teams = [clean(m.group(1)) for m in (RE_TEAM.search(ln) for ln in lines) if m]
        teams = [t for t in teams if t]
        infos = RE_INFOS.search(block)
        shop = RE_SHOP.search(block)
        status = ""
        for ln in lines:
            ms = RE_STATUS.search(ln)
            if ms:
                status = clean(ms.group(1))
                break
        home_is_pauli = (teams and is_stpauli(teams[0])) or ("millerntor" in venue.lower())
        opponent = ""
        for t in teams:
            if not is_stpauli(t):
                opponent = t
                break
        out.append({
            "dt": dt,
            "label": label,
            "venue": venue,
            "teams": teams,
            "opponent": opponent or "?",
            "home": bool(home_is_pauli),
            "infos_url": infos.group(1) if infos else "",
            "infos_slug": slug_of(infos.group(1)) if infos else "",
            "shop_url": shop.group(1) if shop else "",
            "status": status,
            "source": src,
            "sales": [],
        })
    return out


# --------------------------------------------------------------------------
# Artikel-Parser  ->  Verkaufs-/Fristen-Termine
# --------------------------------------------------------------------------
RE_BIS_WD = re.compile(
    r"\bbis\s+(?:einschließlich\s+)?(montag|dienstag|mittwoch|donnerstag|freitag|"
    r"sonnabend|samstag|sonntag)\b")


def _classify_line(line_lower: str):
    """Klassifiziert eine ganze Zeile anhand ihrer Schluesselwoerter.
    Gibt (kind, label) zurueck oder (None, None) wenn unklar/mehrdeutig."""
    has_bis = bool(RE_BIS_WD.search(line_lower)) or "bis einschließlich" in line_lower \
        or "meldeschluss" in line_lower or "angefragt" in line_lower \
        or "anfragen" in line_lower
    has_free = "freier verkauf" in line_lower or "freien verkauf" in line_lower \
        or "freie verkauf" in line_lower
    has_member = "mitgliedervorverkauf" in line_lower or "verkaufsphase 1" in line_lower \
        or "vorverkauf" in line_lower or "mitglied" in line_lower
    has_adk = "adk" in line_lower
    has_zm = "zweitmarkt" in line_lower

    # Es wird NUR der Mitglieder-Vorverkauf aufgenommen.
    # Freier Verkauf, Ticket-Zweitmarkt, ADK und generische Fristen bleiben draussen.
    if has_zm:
        return (None, None)          # kein Ticket-Zweitmarkt
    if has_free:
        return (None, None)          # kein freier Verkauf
    if has_adk and not has_member:
        return (None, None)          # keine ADK-Reservierung
    if has_bis:
        return (None, None)          # generische Fristen weglassen
    if has_member:
        return ("member_sale", "Mitglieder-Vorverkauf")
    return (None, None)


def _extract_match_meta(title: str, body: str, body_l: str):
    """Ermittelt Gegner + Heim/Auswaerts aus Titel/Text eines Ticket-Artikels."""
    t = title
    tl = title.lower()
    if "auswärtsspiel" in tl or "auswaertsspiel" in tl:
        home = False
    elif "heimspiel" in tl:
        home = True
    elif "millerntor" in body_l:
        home = True
    elif re.search(r"\bin\s+[A-ZÄÖÜ]", t):
        home = False
    else:
        home = True
    opp = ""
    m = re.search(r"gegen\s+(?:den |die |das |the )?(.+)$", t)
    if m:
        opp = m.group(1)
    else:
        m = re.search(r"(?:Auswärtsspiel|Auswaertsspiel|Spiel)\s+in\s+(.+)$", t)
        if m:
            opp = m.group(1)
    opp = re.sub(r"\s*[-–].*$", "", opp).strip(" .")
    return home, opp


def parse_article(text: str) -> dict:
    src = source_url_of(text)
    slug = slug_of(src)
    body = text.split("## Themen aus der Gemeinschaft")[0]
    body_l = body.lower()
    h1 = RE_H1.search(body)
    title = clean(h1.group(1)) if h1 else ""

    sales = []
    seen = set()

    def add(kind, label, d, mo, hh, mi):
        key = (kind, int(mo), int(d), hh)
        if key in seen:
            return
        seen.add(key)
        sales.append({"kind": kind, "label": label, "day": int(d), "month": int(mo),
                      "hour": int(hh) if hh else None,
                      "minute": int(mi) if mi else 0})

    # satzweise: jeder Satz/Abschnitt bekommt EINE Klassifikation und nur die
    # Datumstoken desselben Satzes -> vermeidet Vermischung mehrerer Daten je Absatz
    for line in body.splitlines():
        for clause in re.split(r"(?<=[.!;])\s+", line):
            kind, label = _classify_line(clause.lower())
            if not kind:
                continue
            for rx in (RE_ART_DATE_PAREN, RE_ART_DATE_DEN):
                for m in rx.finditer(clause):
                    d, mo, hh, mi = m.groups()
                    add(kind, label, d, mo, hh, mi)

    # Spiel-Metadaten (fuer Ticket-Artikel zu Spielen, die noch nicht im Listing stehen)
    match_dt = None
    is_ticket = slug.startswith("ticket-infos")
    if is_ticket:
        pub = RE_PUBDATE.search(body)
        pub_year = 2000 + int(pub.group(3)) if pub else datetime.now().year
        pub_month = int(pub.group(2)) if pub else 1
        # erstes Paren-Token MIT Uhrzeit = Anstoss
        for m in RE_ART_DATE_PAREN.finditer(body):
            d, mo, hh, mi = m.groups()
            if hh is None:
                continue
            yr = pub_year + 1 if int(mo) < pub_month else pub_year
            try:
                match_dt = datetime(yr, int(mo), int(d), int(hh), int(mi or 0))
            except ValueError:
                match_dt = None
            break

    home, opp = _extract_match_meta(title, body, body_l) if is_ticket else (True, "")
    return {"source": src, "slug": slug, "title": title, "sales": sales,
            "match_dt": match_dt, "home": home, "opponent": opp,
            "is_ticket": is_ticket}


def _fanladen_url(text: str) -> str:
    head = "\n".join(text.splitlines()[:8])
    m = re.search(r"https?://www\.stpauli-fanladen\.de/\S+", head)
    return m.group(0).split("?")[0] if m else ""


def parse_fanladen(text: str) -> dict:
    """Parst einen einzelnen Fanladen-Beitrag (WordPress) zu einer Auswaertsfahrt.
    Liefert Gegner + Spieldatum (aus dem Titel) sowie Bestellfrist-Beginn und
    -Schluss als 'fanladen_open' / 'fanladen_deadline'. Nur datierte Beitrags-
    URLs (/JJJJ/MM/TT/...) werden ausgewertet."""
    url = _fanladen_url(text)
    urlm = RE_FL_URLDATE.search(url)
    if not urlm:
        return {"source": url, "slug": slug_of(url), "sales": [], "match_dt": None,
                "is_ticket": False, "is_fanladen": True}
    pub_year, pub_month = int(urlm.group(1)), int(urlm.group(2))

    title = clean(text.splitlines()[0])
    title = re.sub(r"^»\s*", "", title)
    title = re.sub(r"\s*[-–]\s*FANLADEN.*$", "", title, flags=re.I)
    tl = title.lower()

    # Spieldatum aus dem Titel
    match_dt = None
    dm = RE_FL_TITLE_DATE.search(title)
    if dm:
        d, mo, hh, mi = dm.groups()
        yr = pub_year + 1 if int(mo) < pub_month else pub_year
        hour = int(hh) if hh else 15
        minute = int(mi) if mi else (0 if hh else 30)
        try:
            match_dt = datetime(yr, int(mo), int(d), hour, minute)
        except ValueError:
            match_dt = None

    om = RE_FL_OPP.search(title)
    opp = clean(om.group(1)) if om else (title or "Auswärtsspiel")
    home = ("heimspiel" in tl) and ("auswärts" not in tl)

    sales = []
    seen = set()

    def add(kind, label, d, mo):
        if not match_dt:
            return
        yr = resolve_year(int(d), int(mo), match_dt)
        key = (kind, int(mo), int(d))
        if key in seen:
            return
        seen.add(key)
        sales.append({"kind": kind, "label": label, "day": int(d), "month": int(mo),
                      "hour": None, "minute": 0, "year": yr})

    ms = RE_FL_START.search(text)
    if ms:
        add("fanladen_open", "Fanladen-Bestellung öffnet", ms.group(1), ms.group(2))
    me = RE_FL_END.search(text)
    if me:
        add("fanladen_deadline", "Fanladen-Bestellschluss", me.group(1), me.group(2))

    return {"source": url, "slug": slug_of(url), "title": title, "sales": sales,
            "match_dt": match_dt, "home": home, "opponent": opp,
            "is_ticket": bool(match_dt), "is_fanladen": True}


# --------------------------------------------------------------------------
# Jahr fuer Verkaufsdaten aus dem Spieldatum ableiten
# --------------------------------------------------------------------------
def resolve_year(day: int, month: int, match_dt: datetime) -> int:
    y = match_dt.year
    try:
        cand = date(y, month, day)
    except ValueError:
        return y
    # Verkauf/Frist liegen vor dem Spiel -> ggf. Vorjahr
    if cand > match_dt.date():
        return y - 1
    return y


# --------------------------------------------------------------------------
# Kalender bauen
# --------------------------------------------------------------------------
EMOJI = {
    "member_sale": "🎟️",
    "adk": "🎟️",
    "fanladen_open": "📝",
    "fanladen_deadline": "⏳",
}


def add_alarm(comp, text, trigger):
    al = Alarm()
    al.add("action", "DISPLAY")
    al.add("description", text)
    al.add("trigger", trigger)
    comp.add_component(al)


def uid_for(*parts) -> str:
    h = hashlib.md5("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12]
    return f"{h}@stpauli-tickets"


def build_calendar(matches: list[dict]) -> Calendar:
    cal = Calendar()
    cal.add("prodid", "-//Cowork//FC St. Pauli Ticket-Kalender//DE")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", "FC St. Pauli – Tickets")
    cal.add("x-wr-caldesc", "Spiele + Ticket-Verkaufstermine (automatisch erzeugt)")
    cal.add("x-wr-timezone", "Europe/Berlin")
    cal.add("x-published-ttl", "PT12H")
    cal.add("refresh-interval", timedelta(hours=12), parameters={"VALUE": "DURATION"})
    now = datetime.now(timezone.utc)

    for mt in matches:
        home, away = mt["home"], not mt["home"]
        opp = mt["opponent"]
        side = "Heim" if home else "Auswärts"
        if mt["teams"] and len(mt["teams"]) >= 2:
            pairing = f"{mt['teams'][0]} – {mt['teams'][1]}"
        else:
            pairing = f"St. Pauli – {opp}" if home else f"{opp} – St. Pauli"

        typ = mt["label"] or ""
        typ_suffix = ""
        if re.search(r"test", typ, re.I):
            typ_suffix = " (Test)"
        elif re.search(r"pokal|dfb", typ, re.I):
            typ_suffix = " (Pokal)"

        # --- Spiel-Termin ---
        ev = Event()
        ev.add("uid", uid_for("match", mt["dt"].date(), *sorted(mt["teams"] or [opp])))
        ev.add("summary", f"⚽ {pairing} · {side}{typ_suffix}")
        ev.add("dtstart", to_utc(mt["dt"]))
        ev.add("dtend", to_utc(mt["dt"] + timedelta(hours=2)))
        ev.add("dtstamp", now)
        if mt["venue"]:
            ev.add("location", mt["venue"])
        desc = []
        if typ:
            desc.append(f"Wettbewerb/Art: {typ}")
        desc.append(f"Ort: {mt['venue']}") if mt["venue"] else None
        if mt["status"]:
            desc.append(f"Ticket-Status: {mt['status']}")
        if mt["shop_url"]:
            desc.append(f"Ticket-Onlineshop: {mt['shop_url']}")
        if mt["infos_url"]:
            desc.append(f"Ticket-Infos: {mt['infos_url']}")
        desc.append("(automatisch erzeugt aus fcstpauli.com)")
        ev.add("description", "\n".join(desc))
        add_alarm(ev, f"Heute: {pairing}", timedelta(hours=-4))
        cal.add_component(ev)

        # --- Verkaufs-/Fristen-Termine ---
        for s in mt["sales"]:
            yr = resolve_year(s["day"], s["month"], mt["dt"])
            try:
                d0 = date(yr, s["month"], s["day"])
            except ValueError:
                continue
            emoji = EMOJI.get(s["kind"], "🎟️")
            summ = f"{emoji} {s['label']}: {opp} ({side})"
            ev2 = Event()
            ev2.add("uid", uid_for(s["kind"], d0, opp, side, s.get("hour")))
            ev2.add("summary", summ)
            ev2.add("dtstamp", now)
            sdesc = []
            sdesc.append(f"Spiel: {pairing} am {mt['dt'].strftime('%a %d.%m.%Y %H:%M')}")
            if mt["shop_url"]:
                sdesc.append(f"Onlineshop: {mt['shop_url']}")
            if mt["infos_url"]:
                sdesc.append(f"Infos: {mt['infos_url']}")
            ev2.add("description", "\n".join(sdesc))
            kind = s["kind"]
            if s["hour"] is not None or kind.startswith("fanladen"):
                hh = s["hour"] if s["hour"] is not None else 12
                mm = s["minute"] if s["hour"] is not None else 0
                start = datetime(yr, s["month"], s["day"], hh, mm)
                ev2.add("dtstart", to_utc(start))
                ev2.add("dtend", to_utc(start + timedelta(minutes=30)))
                if kind == "fanladen_open":
                    add_alarm(ev2, summ, timedelta(0))          # genau beim Start
                elif kind == "fanladen_deadline":
                    add_alarm(ev2, summ, timedelta(hours=-2))   # 2 Stunden vor Schluss
                else:  # Mitglieder-Vorverkauf mit Uhrzeit
                    add_alarm(ev2, summ, timedelta(minutes=-15))
                    add_alarm(ev2, summ, timedelta(days=-1))
            else:
                # Mitglieder-Vorverkauf ohne veroeffentlichte Uhrzeit -> ganztags
                ev2.add("dtstart", d0)
                ev2.add("dtend", d0 + timedelta(days=1))
                add_alarm(ev2, summ, timedelta(hours=-15))   # ~09:00 am Vortag (1 Tag vorher)
                add_alarm(ev2, summ, timedelta(hours=9))     # 09:00 am Tag selbst
            cal.add_component(ev2)

    return cal


# --------------------------------------------------------------------------
# Zusammenfuehren + Hauptlauf
# --------------------------------------------------------------------------
def merge_matches(all_matches: list[dict]) -> list[dict]:
    """St. Pauli spielt pro Tag hoechstens einmal -> Zusammenfuehrung per Kalendertag."""
    merged: dict[date, dict] = {}
    for mt in all_matches:
        key = mt["dt"].date()
        if key not in merged:
            merged[key] = mt
            continue
        cur = merged[key]
        if mt.get("home") or "millerntor" in (mt.get("venue") or "").lower():
            cur["home"] = True
        # laengeren/spezifischeren Gegnernamen bevorzugen
        if len(mt.get("opponent") or "") > len(cur.get("opponent") or ""):
            cur["opponent"] = mt["opponent"]
        if len(mt.get("teams") or []) >= 2 and len(cur.get("teams") or []) < 2:
            cur["teams"] = mt["teams"]
        for f in ("infos_url", "infos_slug", "shop_url", "status", "venue"):
            if not cur.get(f) and mt.get(f):
                cur[f] = mt[f]
        if not cur.get("sales") and mt.get("sales"):
            cur["sales"] = mt["sales"]
    return list(merged.values())


def run(input_dir: str, out_path: str) -> dict:
    files = sorted(glob.glob(os.path.join(input_dir, "*")))
    listings, articles = [], {}
    for fp in files:
        if os.path.isdir(fp):
            continue
        try:
            with open(fp, encoding="utf-8") as fh:
                text = fh.read()
        except Exception:
            continue
        if "stpauli-fanladen.de" in text:
            art = parse_fanladen(text)
            if art.get("is_ticket") and art.get("slug"):
                articles[art["slug"]] = art
            continue
        src = source_url_of(text)
        if "## Nächste Begegnungen" in text and ("heimspiele" in src or "auswaertsspiele" in src or "tickets" in src):
            listings.extend(parse_listing(text))
        elif "/news/" in src:
            art = parse_article(text)
            if art["slug"]:
                articles[art["slug"]] = art

    # Spiele auch direkt aus Ticket-Artikeln ableiten (z. B. Spiele, die noch
    # nicht im "Naechste Begegnungen"-Block stehen).
    article_matches = []
    for art in articles.values():
        if art.get("is_ticket") and art.get("match_dt"):
            tl = (art.get("title") or "").lower()
            if "pokal" in tl:
                label = "Pokal"
            elif "testspiel" in tl or "freundschaftsspiel" in tl:
                label = "Testspiel"
            else:
                label = ""
            article_matches.append({
                "dt": art["match_dt"], "label": label, "venue": "", "teams": [],
                "opponent": art.get("opponent") or "?", "home": art.get("home", True),
                "infos_url": art["source"], "infos_slug": art["slug"],
                "shop_url": "", "status": "", "source": art["source"],
                "sales": art.get("sales") or [],
            })

    matches = merge_matches(listings + article_matches)

    # Verkaufstermine an Spiele haengen (per Slug), falls noch nicht gesetzt
    for mt in matches:
        art = articles.get(mt.get("infos_slug"))
        if art and not mt.get("sales"):
            mt["sales"] = art["sales"]

    # Vergangene Spiele (z. B. aus alten Ticket-Artikeln) aussortieren
    cutoff = datetime.now() - timedelta(days=3)
    matches = [mt for mt in matches if mt["dt"] >= cutoff]
    matches.sort(key=lambda m: m["dt"])

    cal = build_calendar(matches)
    data = cal.to_ical()
    with open(out_path, "wb") as fh:
        fh.write(data)

    # Zusammenfassung
    summary = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "listings_parsed": len(listings),
        "matches": len(matches),
        "articles_parsed": len(articles),
        "events": [],
    }
    for mt in matches:
        summary["events"].append({
            "spiel": f"{'St.Pauli' if mt['home'] else mt['opponent']} vs {mt['opponent'] if mt['home'] else 'St.Pauli'}",
            "datum": mt["dt"].strftime("%Y-%m-%d %H:%M"),
            "heim": mt["home"],
            "ort": mt["venue"],
            "verkaufstermine": [
                {"art": s["label"], "datum": f"{s['day']:02d}.{s['month']:02d}." +
                 (f" {s['hour']:02d}:{s['minute']:02d}" if s["hour"] is not None else " (ganztags)")}
                for s in mt["sales"]
            ],
        })
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="input_dir", default="inputs")
    ap.add_argument("--out", dest="out_path", default="st_pauli_tickets.ics")
    ap.add_argument("--summary", dest="summary_path", default="summary.json")
    args = ap.parse_args()

    summary = run(args.input_dir, args.out_path)
    with open(args.summary_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    print(f"OK  ->  {args.out_path}")
    print(f"Spiele: {summary['matches']} | Artikel: {summary['articles_parsed']}")
    for e in summary["events"]:
        vt = "; ".join(f"{v['art']} {v['datum']}" for v in e["verkaufstermine"]) or "—"
        print(f"  • {e['datum']}  {e['spiel']}  [{e['ort']}]  -> {vt}")


if __name__ == "__main__":
    main()

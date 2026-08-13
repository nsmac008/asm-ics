#!/usr/bin/env python3
import hashlib
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dtparse
from dateutil import tz

TZ = tz.gettz("America/New_York")
ASM_LIST = "https://www.asmsyracuse.com/events/index.cfm?th=oncenter"
CRUNCH_SCHEDULE = "https://syracusecrunch.com/sports/mens-ice-hockey/schedule/2026-27"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}

VENUES = {
    "war": {
        "needle": ("upstate medical", "war memorial"),
        "prefix": "War Memorial: ",
        "name": "War Memorial",
        "outfile": "public/asm_warmemorial.ics",
        "location": "Upstate Medical University Arena at The Oncenter War Memorial, 515 Montgomery St, Syracuse, NY 13202",
    },
    "crouse": {
        "needle": ("crouse hinds",),
        "prefix": "Oncenter: ",
        "name": "Oncenter — Crouse Hinds Theater",
        "outfile": "public/oncenter_crousehinds.ics",
        "location": "The Oncenter Crouse Hinds Theater, 411 Montgomery St, Syracuse, NY 13202",
    },
}

DATE_TIME_RE = re.compile(
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)?\w*\s*"
    r"(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+"
    r"(?P<day>\d{1,2})(?:,)?\s+(?P<year>20\d{2})\s*\|?\s*"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>AM|PM)",
    re.I,
)


def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=35)
    r.raise_for_status()
    return r.text


def esc(s):
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def uid_for(source, start, title):
    raw = f"{source}|{start.isoformat()}|{title}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest() + "@nsmac008-venue-feed"


def parse_dt(mon, day, year, hour, minute, ampm):
    return dtparse.parse(f"{mon} {day} {year} {hour}:{minute} {ampm}").replace(tzinfo=TZ)


def event_links_from_listing(html):
    soup = BeautifulSoup(html, "html.parser")
    links = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(ASM_LIST, a["href"])
        if re.search(r"/events/20\d{2}/", href, re.I):
            links.add(href.split("?")[0].rstrip("/"))
    return sorted(links)


def parse_asm_detail(url, html):
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.find("h1")
    title = title_el.get_text(" ", strip=True) if title_el else None
    if not title:
        return []

    text = " ".join(soup.stripped_strings)
    low = text.lower()
    venue_key = None
    for key, cfg in VENUES.items():
        if any(n in low for n in cfg["needle"]):
            venue_key = key
            break
    if not venue_key:
        return []

    starts = []
    for m in DATE_TIME_RE.finditer(text):
        dt = parse_dt(m.group("mon"), m.group("day"), m.group("year"), m.group("hour"), m.group("minute"), m.group("ampm"))
        starts.append(dt)

    # Fallback to explicit Date:/Time: text on ASM detail pages.
    if not starts:
        dm = re.search(r"Date:\s*([A-Za-z]{3,9}\.?\s+\d{1,2}(?:\s*-\s*[A-Za-z]{0,9}\.?\s*\d{1,2})?,?\s+20\d{2})", text, re.I)
        tm = re.search(r"Time:\s*(\d{1,2}:\d{2}\s*(?:AM|PM))", text, re.I)
        if dm and tm:
            try:
                first_date = re.split(r"\s+-\s+", dm.group(1))[0]
                starts.append(dtparse.parse(f"{first_date} {tm.group(1)}").replace(tzinfo=TZ))
            except Exception:
                pass

    # Keep unique future-ish performances.
    cutoff = datetime.now(TZ) - timedelta(days=2)
    unique = []
    seen = set()
    for start in starts:
        if start < cutoff:
            continue
        key = start.isoformat()
        if key in seen:
            continue
        seen.add(key)
        unique.append({
            "title": title,
            "start": start,
            "end": start + timedelta(hours=3),
            "url": url,
            "venue": venue_key,
            "location": VENUES[venue_key]["location"],
        })
    return unique


def parse_crunch_home_games(html):
    soup = BeautifulSoup(html, "html.parser")
    events = []
    cutoff = datetime.now(TZ) - timedelta(days=2)

    # SIDEARM schedule cards are the most reliable structure when present.
    cards = soup.select("li.sidearm-schedule-game, div.sidearm-schedule-game")
    for card in cards:
        text = " ".join(card.stripped_strings)
        if "Upstate Medical University Arena" not in text:
            continue
        date_el = card.select_one(".sidearm-schedule-game-opponent-date, .sidearm-schedule-game-opponent-date-flex")
        opp_el = card.select_one(".sidearm-schedule-game-opponent-name")
        date_text = date_el.get_text(" ", strip=True) if date_el else text
        opponent = opp_el.get_text(" ", strip=True) if opp_el else "Syracuse Crunch Home Game"
        m = re.search(r"(Oct|Nov|Dec|Jan|Feb|Mar|Apr)\.?\s+(\d{1,2}).*?(\d{1,2}(?::\d{2})?\s*(?:a\.m\.|p\.m\.|AM|PM))", date_text, re.I)
        if not m:
            continue
        year = 2026 if m.group(1).lower() in ("oct", "nov", "dec") else 2027
        try:
            start = dtparse.parse(f"{m.group(1)} {m.group(2)} {year} {m.group(3).replace('.', '')}").replace(tzinfo=TZ)
        except Exception:
            continue
        if start < cutoff:
            continue
        url_el = card.find("a", href=True)
        source = urljoin(CRUNCH_SCHEDULE, url_el["href"]) if url_el else CRUNCH_SCHEDULE
        events.append({
            "title": f"Syracuse Crunch vs. {opponent}" if not opponent.lower().startswith("syracuse") else opponent,
            "start": start,
            "end": start + timedelta(hours=3),
            "url": source,
            "venue": "war",
            "location": VENUES["war"]["location"],
        })
    return events


def dedupe(events):
    out = []
    seen = set()
    for e in sorted(events, key=lambda x: x["start"]):
        key = (e["venue"], e["start"].strftime("%Y%m%d%H%M"), re.sub(r"\W+", "", e["title"].lower()))
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def build_ics(events, cal_name, prefix=None):
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//nsmac008//Syracuse Venue Feeds//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{esc(cal_name)}",
        "X-WR-TIMEZONE:America/New_York",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    for e in events:
        p = prefix if prefix is not None else VENUES[e["venue"]]["prefix"]
        summary = p + e["title"]
        start_utc = e["start"].astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        end_utc = e["end"].astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid_for(e['url'], e['start'], e['title'])}",
            f"DTSTAMP:{now}",
            f"DTSTART:{start_utc}",
            f"DTEND:{end_utc}",
            f"SUMMARY:{esc(summary)}",
            f"LOCATION:{esc(e['location'])}",
            f"URL:{esc(e['url'])}",
            f"DESCRIPTION:{esc('Source: ' + e['url'])}",
            "STATUS:CONFIRMED",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)


def main():
    events = []
    listing = fetch(ASM_LIST)
    links = event_links_from_listing(listing)
    print(f"ASM event detail links found: {len(links)}")
    for link in links:
        try:
            parsed = parse_asm_detail(link, fetch(link))
            events.extend(parsed)
        except Exception as exc:
            print(f"WARN {link}: {exc}")

    try:
        events.extend(parse_crunch_home_games(fetch(CRUNCH_SCHEDULE)))
    except Exception as exc:
        print(f"WARN Crunch schedule: {exc}")

    events = dedupe(events)
    war = [e for e in events if e["venue"] == "war"]
    crouse = [e for e in events if e["venue"] == "crouse"]
    print(f"Parsed {len(war)} War Memorial and {len(crouse)} Crouse Hinds performances")

    # Always create the files, even if a source temporarily yields zero events.
    write(VENUES["war"]["outfile"], build_ics(war, "War Memorial" , "War Memorial: "))
    write(VENUES["crouse"]["outfile"], build_ics(crouse, "Oncenter — Crouse Hinds Theater", "Oncenter: "))
    # Backward-compatible combined URL used by the existing Google Calendar subscription.
    write("public/asm_calendar.ics", build_ics(events, "ASM Syracuse — Venue Feed"))


if __name__ == "__main__":
    main()

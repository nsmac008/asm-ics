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
BASE = "https://www.syrvenues.com"
EVENTS_URL = BASE + "/events"
CRUNCH_SCHEDULE = "https://syracusecrunch.com/sports/mens-ice-hockey/schedule/2026-27"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}

VENUES = {
    "war": {
        "page": BASE + "/location/upstate-medical-arena-at-the-oncenter-war-memorial",
        "prefix": "War Memorial: ",
        "name": "War Memorial",
        "outfile": "public/asm_warmemorial.ics",
        "location": "Upstate Medical University Arena at The Oncenter War Memorial, 515 Montgomery St, Syracuse, NY 13202",
    },
    "crouse": {
        "page": BASE + "/location/the-oncenter-crouse-hinds-theater",
        "prefix": "Oncenter: ",
        "name": "Oncenter — Crouse Hinds Theater",
        "outfile": "public/oncenter_crousehinds.ics",
        "location": "The Oncenter Crouse Hinds Theater, 411 Montgomery St, Syracuse, NY 13202",
    },
}

EVENT_PATH_RE = re.compile(r"/events/20\d{2}/[A-Za-z0-9_\-]+", re.I)
PERF_RE = re.compile(
    r"(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+"
    r"(?P<day>\d{1,2}),?\s+(?P<year>20\d{2}).{0,20}?"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>AM|PM)",
    re.I,
)


def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=35, allow_redirects=True)
    r.raise_for_status()
    return r.text


def mirror_url(url):
    return "https://r.jina.ai/http://" + re.sub(r"^https?://", "", url)


def fetch_mirror(url):
    r = requests.get(mirror_url(url), headers={"User-Agent": UA}, timeout=45)
    r.raise_for_status()
    return r.text


def esc(s):
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def uid_for(source, start, title):
    raw = f"{source}|{start.isoformat()}|{title}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest() + "@nsmac008-venue-feed"


def normalize_event_url(value):
    if not value:
        return None
    value = urljoin(BASE, value)
    m = EVENT_PATH_RE.search(value)
    return urljoin(BASE, m.group(0)).rstrip("/") if m else None


def event_links_from_location_html(html):
    links = set()
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        u = normalize_event_url(a["href"])
        if u:
            links.add(u)
    for m in EVENT_PATH_RE.finditer(html):
        links.add(urljoin(BASE, m.group(0)).rstrip("/"))
    return sorted(links)


def event_links_from_location_mirror(text):
    links = set()
    for m in EVENT_PATH_RE.finditer(text):
        links.add(urljoin(BASE, m.group(0)).rstrip("/"))
    return sorted(links)


def discover_links_by_venue():
    mapping = {"war": set(), "crouse": set()}
    for venue, cfg in VENUES.items():
        found = set()
        try:
            html = fetch(cfg["page"])
            found.update(event_links_from_location_html(html))
        except Exception as exc:
            print(f"WARN direct venue discovery {venue}: {exc}")
        if not found:
            try:
                text = fetch_mirror(cfg["page"])
                found.update(event_links_from_location_mirror(text))
            except Exception as exc:
                print(f"WARN mirror venue discovery {venue}: {exc}")
        mapping[venue].update(found)
        print(f"Venue discovery {venue}: {len(found)} event links")
    return mapping


def title_from_html(html):
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    return h1.get_text(" ", strip=True) if h1 else None


def title_from_mirror(text):
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return None


def page_text(html):
    return " ".join(BeautifulSoup(html, "html.parser").stripped_strings)


def parse_performances(text):
    starts = []
    for m in PERF_RE.finditer(text):
        try:
            starts.append(
                dtparse.parse(
                    f"{m.group('mon')} {m.group('day')} {m.group('year')} "
                    f"{m.group('hour')}:{m.group('minute')} {m.group('ampm')}"
                ).replace(tzinfo=TZ)
            )
        except Exception:
            pass

    if not starts:
        dm = re.search(r"Date:\s*([A-Za-z]{3,9}\.?\s+\d{1,2},?\s+20\d{2})", text, re.I)
        times = re.findall(r"(?:Time:\s*)?(\d{1,2}:\d{2}\s*(?:AM|PM))", text, re.I)
        if dm and times:
            for t in times[:8]:
                try:
                    starts.append(dtparse.parse(f"{dm.group(1)} {t}").replace(tzinfo=TZ))
                except Exception:
                    pass

    cutoff = datetime.now(TZ) - timedelta(days=2)
    out, seen = [], set()
    for start in sorted(starts):
        if start < cutoff:
            continue
        key = start.isoformat()
        if key in seen:
            continue
        seen.add(key)
        out.append(start)
    return out


def make_events(url, title, venue, starts):
    low = title.lower()
    if "cancelled" in low or "canceled" in low or "postponed" in low:
        return []
    return [
        {
            "title": title,
            "start": start,
            "end": start + timedelta(hours=3),
            "url": url,
            "venue": venue,
            "location": VENUES[venue]["location"],
        }
        for start in starts
    ]


def parse_event_detail(url, venue):
    try:
        html = fetch(url)
        title = title_from_html(html)
        starts = parse_performances(page_text(html))
        if title and starts:
            return make_events(url, title, venue, starts)
    except Exception as exc:
        print(f"WARN direct detail {url}: {exc}")

    try:
        text = fetch_mirror(url)
        title = title_from_mirror(text)
        starts = parse_performances(" ".join(text.split()))
        if title and starts:
            return make_events(url, title, venue, starts)
    except Exception as exc:
        print(f"WARN mirror detail {url}: {exc}")
    return []


def parse_crunch_home_games(html):
    soup = BeautifulSoup(html, "html.parser")
    events = []
    cutoff = datetime.now(TZ) - timedelta(days=2)
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
        a = card.find("a", href=True)
        source = urljoin(CRUNCH_SCHEDULE, a["href"]) if a else CRUNCH_SCHEDULE
        title = opponent if opponent.lower().startswith("syracuse") else f"Syracuse Crunch vs. {opponent}"
        events.append({
            "title": title,
            "start": start,
            "end": start + timedelta(hours=3),
            "url": source,
            "venue": "war",
            "location": VENUES["war"]["location"],
        })
    return events


def dedupe(events):
    out, seen = [], set()
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
        start_utc = e["start"].astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        end_utc = e["end"].astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid_for(e['url'], e['start'], e['title'])}",
            f"DTSTAMP:{now}",
            f"DTSTART:{start_utc}",
            f"DTEND:{end_utc}",
            f"SUMMARY:{esc(p + e['title'])}",
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
    mapping = discover_links_by_venue()

    for venue, links in mapping.items():
        for link in sorted(links):
            parsed = parse_event_detail(link, venue)
            if parsed:
                print(f"{venue}: {link} -> {len(parsed)} performance(s)")
            events.extend(parsed)

    try:
        crunch = parse_crunch_home_games(fetch(CRUNCH_SCHEDULE))
        print(f"Crunch home games found: {len(crunch)}")
        events.extend(crunch)
    except Exception as exc:
        print(f"WARN Crunch schedule: {exc}")

    events = dedupe(events)
    war = [e for e in events if e["venue"] == "war"]
    crouse = [e for e in events if e["venue"] == "crouse"]
    print(f"FINAL: {len(war)} War Memorial and {len(crouse)} Crouse Hinds performances")

    write(VENUES["war"]["outfile"], build_ics(war, "War Memorial", "War Memorial: "))
    write(VENUES["crouse"]["outfile"], build_ics(crouse, "Oncenter — Crouse Hinds Theater", "Oncenter: "))
    write("public/asm_calendar.ics", build_ics(events, "ASM Syracuse — Venue Feed"))


if __name__ == "__main__":
    main()

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
        "needle": ("upstate medical arena", "war memorial"),
        "prefix": "War Memorial: ",
        "name": "War Memorial",
        "outfile": "public/asm_warmemorial.ics",
        "location": "Upstate Medical University Arena at The Oncenter War Memorial, 515 Montgomery St, Syracuse, NY 13202",
    },
    "crouse": {
        "page": BASE + "/location/the-oncenter-crouse-hinds-theater",
        "needle": ("crouse hinds",),
        "prefix": "Oncenter: ",
        "name": "Oncenter — Crouse Hinds Theater",
        "outfile": "public/oncenter_crousehinds.ics",
        "location": "The Oncenter Crouse Hinds Theater, 411 Montgomery St, Syracuse, NY 13202",
    },
}

EVENT_URL_RE = re.compile(r"https?://(?:www\.)?syrvenues\.com/events/20\d{2}/[A-Za-z0-9_\-]+", re.I)
PERFORMANCE_RE = re.compile(
    r"(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+"
    r"(?P<day>\d{1,2}),?\s+(?P<year>20\d{2})\s*(?:\||[-–—])?\s*"
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
    if value.startswith("/"):
        value = urljoin(BASE, value)
    m = EVENT_URL_RE.search(value)
    return m.group(0).rstrip("/") if m else None


def links_from_html(html):
    links = set()
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        u = normalize_event_url(urljoin(BASE, a["href"]))
        if u:
            links.add(u)
    for m in re.finditer(r"(?:https?://(?:www\.)?syrvenues\.com)?(/events/20\d{2}/[A-Za-z0-9_\-]+)", html, re.I):
        u = normalize_event_url(urljoin(BASE, m.group(1)))
        if u:
            links.add(u)
    return links


def links_from_mirror(text):
    links = set(EVENT_URL_RE.findall(text))
    for m in re.finditer(r"\]\((/events/20\d{2}/[A-Za-z0-9_\-]+)\)", text, re.I):
        links.add(urljoin(BASE, m.group(1)))
    return {u.rstrip("/") for u in links}


def discover_event_links():
    links = set()
    sources = [EVENTS_URL] + [cfg["page"] for cfg in VENUES.values()]
    for source in sources:
        direct_links = set()
        try:
            direct = fetch(source)
            direct_links = links_from_html(direct)
            links.update(direct_links)
        except Exception as exc:
            print(f"WARN direct discovery {source}: {exc}")
        if not direct_links:
            try:
                mirrored = fetch_mirror(source)
                found = links_from_mirror(mirrored)
                links.update(found)
                print(f"Mirror discovery {source}: {len(found)} links")
            except Exception as exc:
                print(f"WARN mirror discovery {source}: {exc}")
    return sorted(links)


def page_text(html):
    return " ".join(BeautifulSoup(html, "html.parser").stripped_strings)


def title_from_content(content, is_html=True):
    if is_html:
        soup = BeautifulSoup(content, "html.parser")
        h1 = soup.find("h1")
        if h1:
            return h1.get_text(" ", strip=True)
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return None


def venue_from_text(text):
    low = text.lower()
    for key, cfg in VENUES.items():
        if any(n in low for n in cfg["needle"]):
            return key
    return None


def parse_performances(text):
    starts = []
    for m in PERFORMANCE_RE.finditer(text):
        try:
            dt = dtparse.parse(
                f"{m.group('mon')} {m.group('day')} {m.group('year')} "
                f"{m.group('hour')}:{m.group('minute')} {m.group('ampm')}"
            ).replace(tzinfo=TZ)
            starts.append(dt)
        except Exception:
            pass

    # Main Date:/Time: fields catch simple one-performance pages.
    if not starts:
        dm = re.search(r"Date:\s*([A-Za-z]{3,9}\.?\s+\d{1,2},?\s+20\d{2})", text, re.I)
        tm = re.search(r"Time:\s*(\d{1,2}:\d{2}\s*(?:AM|PM))", text, re.I)
        if dm and tm:
            try:
                starts.append(dtparse.parse(f"{dm.group(1)} {tm.group(1)}").replace(tzinfo=TZ))
            except Exception:
                pass

    cutoff = datetime.now(TZ) - timedelta(days=2)
    out = []
    seen = set()
    for dt in sorted(starts):
        if dt < cutoff:
            continue
        key = dt.isoformat()
        if key not in seen:
            seen.add(key)
            out.append(dt)
    return out


def parse_event_detail(url):
    content = None
    is_html = True
    try:
        content = fetch(url)
    except Exception as exc:
        print(f"WARN direct detail {url}: {exc}")
    if content:
        text = page_text(content)
        title = title_from_content(content, True)
        venue = venue_from_text(text)
        starts = parse_performances(text)
        if title and venue and starts:
            return make_events(url, title, venue, starts)

    try:
        content = fetch_mirror(url)
        is_html = False
    except Exception as exc:
        print(f"WARN mirror detail {url}: {exc}")
        return []

    text = " ".join(content.split())
    title = title_from_content(content, is_html)
    venue = venue_from_text(text)
    starts = parse_performances(text)
    if not title or not venue or not starts:
        return []
    return make_events(url, title, venue, starts)


def make_events(url, title, venue, starts):
    low_title = title.lower()
    if "cancelled" in low_title or "canceled" in low_title or "postponed" in low_title:
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
    links = discover_event_links()
    print(f"Syracuse venue event detail links found: {len(links)}")
    for link in links:
        try:
            parsed = parse_event_detail(link)
            if parsed:
                print(f"  {link}: {len(parsed)} performance(s), venue={parsed[0]['venue']}")
            events.extend(parsed)
        except Exception as exc:
            print(f"WARN {link}: {exc}")

    try:
        crunch = parse_crunch_home_games(fetch(CRUNCH_SCHEDULE))
        print(f"Crunch home games found: {len(crunch)}")
        events.extend(crunch)
    except Exception as exc:
        print(f"WARN Crunch schedule: {exc}")

    events = dedupe(events)
    war = [e for e in events if e["venue"] == "war"]
    crouse = [e for e in events if e["venue"] == "crouse"]
    print(f"Parsed {len(war)} War Memorial and {len(crouse)} Crouse Hinds performances")

    write(VENUES["war"]["outfile"], build_ics(war, "War Memorial", "War Memorial: "))
    write(VENUES["crouse"]["outfile"], build_ics(crouse, "Oncenter — Crouse Hinds Theater", "Oncenter: "))
    write("public/asm_calendar.ics", build_ics(events, "ASM Syracuse — Venue Feed"))


if __name__ == "__main__":
    main()

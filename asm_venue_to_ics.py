#!/usr/bin/env python3
"""
ASM Syracuse venue/location → auto-updating ICS
v9 — robust logging, safe fallback event, public/ output, configurable TITLE_PREFIX

Run locally:
  python asm_venue_to_ics.py [optional_venue_url] [--debug]

Outputs:
  public/asm_calendar.ics
  asm_debug.log (when --debug)
"""
import os
import re
import sys
import json
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dateutil import tz

# ── Config ─────────────────────────────────────────────────────────────────────
SITE_TZ = tz.gettz("America/New_York")
DEFAULT_EVENT_DURATION_HOURS = 2
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
DEFAULT_URL = (
    "https://www.asmsyracuse.com/location/"
    "upstate-medical-arena-at-the-oncenter-war-memorial?ev=690&th=fairgrounds"
)
OUT_DIR = "public"
OUT_FILE = os.path.join(OUT_DIR, "asm_calendar.ics")
TITLE_PREFIX = "ASM: "  # change to "" if you don't want a prefix

# Event detail links
EVENT_HREF_RE = re.compile(
    r"https?://(?:www\.)?asmsyracuse\.com/(?:events|event)/[A-Za-z0-9\-/]+/?",
    re.I,
)

# JSON-LD etc.
JSON_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
ITEMPROP_STARTDATE_RE = re.compile(
    r'itemprop=["\']startDate["\'][^>]*?(?:datetime=["\']([^"\']+)["\']|content=["\']([^"\']+)["\'])',
    re.I,
)
TIME_TAG_RE = re.compile(r'<time[^>]+datetime=["\']([^"\']+)["\']', re.I)
META_START_RE = re.compile(
    r'<meta[^>]+property=["\']event:start_time["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)
GENERIC_STARTDATE_RE = re.compile(r'"startDate"\s*:\s*["\']([^"\']+)["\']')

# Visible text date like "Oct 24, 2025 | 7:00 PM" / "October 24 at 7:00 PM"
MONTH = (
    r'(January|February|March|April|May|June|July|August|September|October|November|December|'
    r'Jan\.?|Feb\.?|Mar\.?|Apr\.?|May|Jun\.?|Jul\.?|Aug\.?|Sep\.?|Sept\.?|Oct\.?|Nov\.?|Dec\.?)'
)
TIME12 = r'(\d{1,2})(?::(\d{2}))?\s*([AaPp][Mm])'
DATE_TEXT_RE = re.compile(
    rf'\b{MONTH}\s+(\d{{1,2}})(?:,\s*(\d{{4}}))?(?:\s*(?:[•@\-–\|])\s*|\s+at\s+){TIME12}\b'
)
MONTH_MAP = {
    'January':1,'Jan.':1,'Jan':1,
    'February':2,'Feb.':2,'Feb':2,
    'March':3,'Mar.':3,
    'April':4,'Apr.':4,'Apr':4,
    'May':5,
    'June':6,'Jun.':6,'Jun':6,
    'July':7,'Jul.':7,'Jul':7,
    'August':8,'Aug.':8,'Aug':8,
    'September':9,'Sep.':9,'Sept.':9,'Sep':9,'Sept':9,
    'October':10,'Oct.':10,'Oct':10,
    'November':11,'Nov.':11,'Nov':11,
    'December':12,'Dec.':12,'Dec':12,
}

DEBUG = "--debug" in sys.argv
LOG_FILE = "asm_debug.log"
def log(msg):
    line = f"DEBUG: {msg}"
    if DEBUG:
        print(line)
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

# ── Helpers ────────────────────────────────────────────────────────────────────
def now_ny() -> datetime:
    return datetime.now(tz=SITE_TZ)

def fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text

def escape_ics(s: str) -> str:
    return (
        (s or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )

def utcstamp(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

class Event:
    def __init__(self, title, start, end=None, url=None):
        base = (title or "ASM Syracuse Event").strip()
        self.title = f"{TITLE_PREFIX}{base}".strip()
        self.start = start
        self.end = end or (start + timedelta(hours=DEFAULT_EVENT_DURATION_HOURS))
        self.url = url
        self.uid = f"{uuid.uuid4()}@asmsyracuse.com"

    def to_ics(self) -> str:
        lines = [
            "BEGIN:VEVENT",
            f"UID:{self.uid}",
            f"DTSTAMP:{utcstamp(now_ny())}",
            f"DTSTART:{utcstamp(self.start)}",
            f"DTEND:{utcstamp(self.end)}",
            f"SUMMARY:{escape_ics(self.title)}",
        ]
        if self.url:
            lines.append(f"URL:{escape_ics(self.url)}")
        lines.append("END:VEVENT")
        return "\n".join(lines)

def parse_iso_like(iso: str | None) -> datetime | None:
    if not iso:
        return None
    s = iso.strip().replace(" ", "T")
    # If date-only, assume 7:00 PM local
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        s += "T19:00:00"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SITE_TZ)
    return dt

def parse_json_ld(html: str):
    out = []
    for m in JSON_LD_RE.finditer(html):
        raw = m.group(1).strip()
        try:
            out.append(json.loads(raw))
        except Exception:
            pass
    return out

def walk(n):
    if isinstance(n, dict):
        yield n
        for v in n.values():
            yield from walk(v)
    elif isinstance(n, list):
        for x in n:
            yield from walk(x)

def extract_title_from_html(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    if h1:
        t = h1.get_text(" ", strip=True)
        if t: return t
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].strip()
    t = soup.find("title")
    if t:
        return t.get_text(" ", strip=True).split("|")[0].strip()
    return None

def find_dates_and_title_from_html(html: str):
    dates = []
    title = None

    # 1) JSON-LD Event
    for block in parse_json_ld(html):
        for node in walk(block):
            if "Event" in str(node.get("@type", "")):
                if not title and isinstance(node.get("name"), str):
                    title = node["name"]
                dt = parse_iso_like(
                    node.get("startDate") or node.get("start") or node.get("startTime")
                )
                if dt:
                    dates.append(dt)
    if dates:
        return dates, title

    # 2) itemprop / time / meta / generic JSON
    for m in ITEMPROP_STARTDATE_RE.finditer(html):
        dt = parse_iso_like(m.group(1) or m.group(2))
        if dt: dates.append(dt)
    for m in TIME_TAG_RE.finditer(html):
        dt = parse_iso_like(m.group(1))
        if dt: dates.append(dt)
    for m in META_START_RE.finditer(html):
        dt = parse_iso_like(m.group(1))
        if dt: dates.append(dt)
    for m in GENERIC_STARTDATE_RE.finditer(html):
        dt = parse_iso_like(m.group(1))
        if dt: dates.append(dt)
    if dates:
        return dates, title

    # 3) Visible text patterns
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    for m in DATE_TEXT_RE.finditer(text):
        mon_name = m.group(1); day = int(m.group(2)); year = m.group(3)
        h = int(m.group(4)); mm = int(m.group(5) or 0); ap = m.group(6).lower()
        mon = MONTH_MAP.get(mon_name)
        if not mon: continue
        if ap == "pm" and h != 12: h += 12
        if ap == "am" and h == 12: h = 0
        y = int(year) if year else now_ny().year
        dates.append(datetime(y, mon, day, h, mm, tzinfo=SITE_TZ))

    return dates, title

def collect_event_links_from_location(html: str, base_url: str) -> list[str]:
    links = set()
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/"):
            href = urljoin(base_url, href)
        if EVENT_HREF_RE.match(href):
            links.add(href.split("?")[0].rstrip("/") + "/")
    # also check JSON blobs with "url": "..."
    for m in re.finditer(r'"url"\s*:\s*"([^"]+)"', html):
        href = m.group(1)
        if href.startswith("/"):
            href = urljoin(base_url, href)
        if EVENT_HREF_RE.match(href):
            links.add(href.split("?")[0].rstrip("/") + "/")
    return sorted(links)

def build_ics(events):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//asm-venue-ics v9//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:ASM Syracuse — Venue Feed",
        "X-WR-TIMEZONE:America/New_York",
    ]
    for e in events:
        lines.append(e.to_ics())
    lines.append("END:VCALENDAR")
    return "\n".join(lines) + "\n"

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if DEBUG and os.path.exists(LOG_FILE):
        try: os.remove(LOG_FILE)
        except Exception: pass

    start_url = None
    for arg in sys.argv[1:]:
        if arg.startswith("http"):
            start_url = arg
    if not start_url:
        start_url = DEFAULT_URL

    log(f"start_url = {start_url}")
    try:
        html = fetch(start_url)
    except Exception as e:
        log(f"ERROR fetching start_url: {e}")
        html = ""

    if not html:
        log("Empty HTML for start_url")
        # still emit a feed so Pages deploys (with a visible test event)
        s = now_ny() + timedelta(days=1, hours=9)
        events = [Event("Venue Feed Connected — awaiting events", s, s + timedelta(hours=1), url=start_url)]
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(OUT_FILE, "w", encoding="utf-8") as f:
            f.write(build_ics(events))
        print(f"Wrote {OUT_FILE} with 1 placeholder event (failed to fetch start page).")
        if DEBUG:
            with open(LOG_FILE, "a", encoding="utf-8") as f: f.write("Start page fetch failed.\n")
        return

    log(f"start_html_length = {len(html)}")
    # print first 1000 chars to log
    log("start_html_head = " + html[:1000].replace("\n","\\n"))

    event_urls = collect_event_links_from_location(html, start_url)
    log(f"event_urls_found = {len(event_urls)}")
    for i,u in enumerate(event_urls[:30],1):
        log(f"{i}. {u}")

    events = []

    if not event_urls:
        ds, title = find_dates_and_title_from_html(html)
        log(f"direct_dates = {len(ds)} ; title = {title!r}")
        if not title:
            title = extract_title_from_html(html) or "ASM Syracuse Event"
        for d in ds:
            if d >= now_ny() - timedelta(days=1):
                events.append(Event(title, d, url=start_url))

    for u in event_urls:
        try:
            eh = fetch(u)
        except Exception as e:
            log(f"ERROR fetching event {u}: {e}")
            continue
        ds, title = find_dates_and_title_from_html(eh)
        log(f"event {u} → dates={len(ds)} title={title!r}")
        if not title:
            title = extract_title_from_html(eh) or "ASM Syracuse Event"
        for d in ds:
            if d >= now_ny() - timedelta(days=1):
                events.append(Event(title, d, url=u))

    if not events:
        s = now_ny() + timedelta(days=1, hours=9)
        events.append(Event("Venue Feed Connected — awaiting events", s, s + timedelta(hours=1), url=start_url))
        log("No events parsed; wrote placeholder.")

    os.makedirs(OUT_DIR, exist_ok=True)
    events.sort(key=lambda e: e.start)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(build_ics(events))
    print(f"Wrote {OUT_FILE} with {len(events)} events from {len(event_urls)} event URLs.")
    if DEBUG:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"TOTAL events written: {len(events)}\n")

if __name__ == "__main__":
    main()

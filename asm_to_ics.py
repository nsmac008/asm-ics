#!/usr/bin/env python3
import re
import sys
import uuid
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dateutil import tz

BASE = "https://www.asmsyracuse.com"
LIST_URL = f"{BASE}/events"
AMP_URL = f"{BASE}/events/amp/"
SITE_TZ = tz.gettz("America/New_York")
DEFAULT_EVENT_DURATION_HOURS = 2
HEADERS = {"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"}

EVENT_HREF_RE = re.compile(r"https?://(?:www\.)?asmsyracuse\.com/(?:events|event)/[a-z0-9\-/]+/?", re.I)
JSON_LD_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I|re.S)
ITEMPROP_STARTDATE_RE = re.compile(r'itemprop=["\']startDate["\'][^>]*?(?:datetime=["\']([^"\']+)["\']|content=["\']([^"\']+)["\'])', re.I)
TIME_TAG_RE = re.compile(r'<time[^>]+datetime=["\']([^"\']+)["\']', re.I)
GENERIC_STARTDATE_RE = re.compile(r'"startDate"\s*:\s*["\']([^"\']+)["\']')
META_START_RE = re.compile(r'<meta[^>]+property=["\']event:start_time["\'][^>]+content=["\']([^"\']+)["\']', re.I)

# Loose text patterns like "October 25, 2025 @ 7:30 PM" or "Oct 25 • 7:30 PM"
MONTH = r'(January|February|March|April|May|June|July|August|September|October|November|December|Jan\.?|Feb\.?|Mar\.?|Apr\.?|May|Jun\.?|Jul\.?|Aug\.?|Sep\.?|Sept\.?|Oct\.?|Nov\.?|Dec\.?)'
TIME12 = r'(\d{1,2})(?::(\d{2}))?\s*([AaPp][Mm])'
DATE_TEXT_RE = re.compile(
     rf'\b{MONTH}\s+(\d{{1,2}})(?:,\s*(\d{{4}}))?(?:\s*[•@\-–\|]\s*|\s+at\s+){TIME12}\b'
)

MONTH_MAP = {
    'January':1,'Jan.':1,'Jan':1,
    'February':2,'Feb.':2,'Feb':2,
    'March':3,'Mar.':3,'Mar':3,
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

def now_ny(): return datetime.now(tz=SITE_TZ)

def escape_ics(s: str) -> str:
    return (s or "").replace("\\","\\\\").replace(";","\\;").replace(",","\\,").replace("\n","\\n")

def utcstamp(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

class Event:
    def __init__(self, title, start, end=None, url=None):
        self.title = (title or "ASM Syracuse Event").strip()
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
        if self.url: lines.append(f"URL:{escape_ics(self.url)}")
        lines.append("END:VEVENT")
        return "\n".join(lines)

def fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text

def collect_links_from_html(html: str) -> set[str]:
    out = set()
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/"):
            href = urljoin(BASE, href)
        m = EVENT_HREF_RE.match(href)
        if m and "/events/amp/" not in href:
            out.add(href.split("?")[0].rstrip("/") + "/")
    for m in EVENT_HREF_RE.finditer(html):
        href = m.group(0)
        if "/events/amp/" not in href:
            out.add(href.split("?")[0].rstrip("/") + "/")
    return out

def parse_json_ld(html: str):
    items = []
    for m in JSON_LD_RE.finditer(html):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
            items.append(data)
        except Exception:
            pass
    return items

def walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for x in node:
            yield from walk(x)

def parse_iso_like(iso: str|None) -> datetime|None:
    if not iso: return None
    s = iso.strip().replace(" ", "T")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        s += "T19:00:00"
    try:
        dt = datetime.fromisoformat(s.replace("Z","+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None: dt = dt.replace(tzinfo=SITE_TZ)
    return dt

def find_event_datetimes(html: str) -> tuple[list[datetime], str|None]:
    dates = []
    title = None
    # 1) JSON-LD
    for block in parse_json_ld(html):
        for node in walk(block):
            t = str(node.get("@type",""))
            if "Event" in t:
                if not title and isinstance(node.get("name"), str):
                    title = node["name"]
                iso = node.get("startDate") or node.get("start") or node.get("startTime")
                d = parse_iso_like(iso)
                if d: dates.append(d)
    if dates: return dates, title
    # 2) itemprop
    for m in ITEMPROP_STARTDATE_RE.finditer(html):
        iso = m.group(1) or m.group(2)
        d = parse_iso_like(iso)
        if d: dates.append(d)
    if dates: return dates, title
    # 3) time tags
    for m in TIME_TAG_RE.finditer(html):
        d = parse_iso_like(m.group(1))
        if d: dates.append(d)
    if dates: return dates, title
    # 4) meta
    for m in META_START_RE.finditer(html):
        d = parse_iso_like(m.group(1))
        if d: dates.append(d)
    if dates: return dates, title
    # 5) generic JSON "startDate":"..."
    for m in GENERIC_STARTDATE_RE.finditer(html):
        d = parse_iso_like(m.group(1))
        if d: dates.append(d)
    if dates: return dates, title
    # 6) visible text like "October 25, 2025 @ 7:30 PM"
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    for m in DATE_TEXT_RE.finditer(text):
        mon_name = m.group(1)
        day = int(m.group(2))
        year = m.group(3)
        h = int(m.group(4))
        mm = int(m.group(5) or 0)
        ampm = m.group(6).lower()
        mon = MONTH_MAP.get(mon_name, None)
        if not mon: continue
        if ampm == "pm" and h != 12: h += 12
        if ampm == "am" and h == 12: h = 0
        y = int(year) if year else now_ny().year
        dt = datetime(y, mon, day, h, mm, tzinfo=SITE_TZ)
        dates.append(dt)
    return dates, title

def extract_title(html: str) -> str|None:
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.I|re.S)
    if m:
        return BeautifulSoup(m.group(1), "html.parser").get_text(" ", strip=True)
    t = re.search(r"<title>(.*?)</title>", html, re.I|re.S)
    if t:
        txt = BeautifulSoup(t.group(1), "html.parser").get_text(" ", strip=True)
        return txt.split("|")[0].strip()
    return None

def main():
    list_html = fetch(LIST_URL)
    urls = collect_links_from_html(list_html)
    try:
        amp_html = fetch(AMP_URL)
        urls |= collect_links_from_html(amp_html)
    except Exception:
        pass

    print(f"DEBUG: collected {len(urls)} event URLs")
    for u in sorted(urls)[:30]:
        print("URL:", u)

    events = []
    if not urls:
        # As a fallback, try to parse events directly from list pages' JSON-LD
        for html, page in ((list_html, LIST_URL), (amp_html if 'amp_html' in locals() else '', AMP_URL)):
            for block in parse_json_ld(html or ""):
                for node in walk(block):
                    if str(node.get("@type","")).lower() == "event":
                        name = node.get("name") or "ASM Syracuse Event"
                        start = parse_iso_like(node.get("startDate") or node.get("start"))
                        if start:
                            url = node.get("url") or page
                            if isinstance(url, dict): url = url.get("@id") or page
                            events.append(Event(name, start, url=url))

    # Visit each event page
    for u in sorted(urls):
        try:
            html = fetch(u)
        except Exception as e:
            print(f"Fetch failed: {u} :: {e}", file=sys.stderr)
            continue
        dates, title = find_event_datetimes(html)
        if not dates:
            print(f"No dates on page: {u}", file=sys.stderr)
            continue
        title = title or extract_title(html) or "ASM Syracuse Event"
        for dt in dates:
            if dt >= now_ny() - timedelta(days=1):
                events.append(Event(title, dt, url=u))

    if not events:
        # Emit a visible test event so subscribers can verify the feed is live
        start = now_ny() + timedelta(days=1, hours=9)
        events.append(Event("ASM Feed Connected — awaiting events", start, end=start + timedelta(hours=1), url=LIST_URL))

    events.sort(key=lambda e: e.start)

    with open("asm_calendar.ics", "w", encoding="utf-8") as f:
        f.write(
            "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//asm-syracuse-ics v4//EN\nCALSCALE:GREGORIAN\nMETHOD:PUBLISH\nX-WR-CALNAME:ASM Syracuse\nX-WR-TIMEZONE:America/New_York\n"
            + "\n".join(ev.to_ics() for ev in events) +
            "\nEND:VCALENDAR\n"
        )
    print(f"Wrote asm_calendar.ics with {len(events)} events.")

if __name__ == "__main__":
    main()

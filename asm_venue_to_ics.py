#!/usr/bin/env python3
# ASM Syracuse venue/location page → ICS (v5)
import re, sys, json, uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from dateutil import tz

SITE_TZ = tz.gettz("America/New_York")
DEFAULT_EVENT_DURATION_HOURS = 2
HEADERS = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}
DEFAULT_URL = "https://www.asmsyracuse.com/location/upstate-medical-arena-at-the-oncenter-war-memorial?ev=690&th=fairgrounds"

EVENT_HREF_RE = re.compile(r"https?://(?:www\.)?asmsyracuse\.com/(?:events|event)/[a-z0-9\-/]+/?", re.I)
JSON_LD_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I|re.S)
ITEMPROP_STARTDATE_RE = re.compile(r'itemprop=["\']startDate["\'][^>]*?(?:datetime=["\']([^"\']+)["\']|content=["\']([^"\']+)["\'])', re.I)
TIME_TAG_RE = re.compile(r'<time[^>]+datetime=["\']([^"\']+)["\']', re.I)
META_START_RE = re.compile(r'<meta[^>]+property=["\']event:start_time["\'][^>]+content=["\']([^"\']+)["\']', re.I)
GENERIC_STARTDATE_RE = re.compile(r'"startDate"\s*:\s*["\']([^"\']+)["\']')

MONTH = r'(January|February|March|April|May|June|July|August|September|October|November|December|Jan\.?|Feb\.?|Mar\.?|Apr\.?|May|Jun\.?|Jul\.?|Aug\.?|Sep\.?|Sept\.?|Oct\.?|Nov\.?|Dec\.?)'
TIME12 = r'(\d{1,2})(?::(\d{2}))?\s*([AaPp][Mm])'
DATE_TEXT_RE = re.compile(rf'\b{MONTH}\s+(\d{{1,2}})(?:,\s*(\d{{4}}))?(?:\s*(?:[•@\-–\|])\s*|\s+at\s+){TIME12}\b')

MONTH_MAP = {'January':1,'Jan.':1,'Jan':1,'February':2,'Feb.':2,'Feb':2,'March':3,'Mar.':3,'April':4,'Apr.':4,'Apr':4,'May':5,'June':6,'Jun.':6,'Jun':6,'July':7,'Jul.':7,'Jul':7,'August':8,'Aug.':8,'Aug':8,'September':9,'Sep.':9,'Sept.':9,'Sep':9,'Sept':9,'October':10,'Oct.':10,'Oct':10,'November':11,'Nov.':11,'Nov':11,'December':12,'Dec.':12,'Dec':12}

def now_ny(): return datetime.now(tz=SITE_TZ)

def fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text

def escape_ics(s: str) -> str:
    return (s or "").replace("\\","\\\\").replace(";","\\;").replace(",","\\,").replace("\n","\\n")

def utcstamp(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

class Event:
    def __init__(self, title, start, end=None, url=None):
        self.title = (title or "ASM Syracuse Event").strip()
        self.start = start
        self.end = end or (start + timedelta(hours=2))
        self.url = url
        self.uid = f"{uuid.uuid4()}@asmsyracuse.com"
    def to_ics(self):
        lines = ["BEGIN:VEVENT", f"UID:{self.uid}", f"DTSTAMP:{utcstamp(now_ny())}", f"DTSTART:{utcstamp(self.start)}", f"DTEND:{utcstamp(self.end)}", f"SUMMARY:{escape_ics(self.title)}"]
        if self.url: lines.append(f"URL:{escape_ics(self.url)}")
        lines.append("END:VEVENT")
        return "\n".join(lines)

def parse_iso_like(iso):
    if not iso: return None
    s = iso.strip().replace(" ", "T")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s): s += "T19:00:00"
    try:
        dt = datetime.fromisoformat(s.replace("Z","+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None: dt = dt.replace(tzinfo=SITE_TZ)
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

def find_dates_and_title_from_html(html: str):
    dates, title = [], None
    # JSON-LD Event
    for block in parse_json_ld(html):
        for node in walk(block):
            if "Event" in str(node.get("@type","")):
                if not title and isinstance(node.get("name"), str):
                    title = node["name"]
                dt = parse_iso_like(node.get("startDate") or node.get("start") or node.get("startTime"))
                if dt: dates.append(dt)
    if dates: return dates, title
    # itemprop/time/meta/generic
    for m in ITEMPROP_STARTDATE_RE.finditer(html):
        dt = parse_iso_like(m.group(1) or m.group(2)); 
        if dt: dates.append(dt)
    for m in TIME_TAG_RE.finditer(html):
        dt = parse_iso_like(m.group(1)); 
        if dt: dates.append(dt)
    for m in META_START_RE.finditer(html):
        dt = parse_iso_like(m.group(1)); 
        if dt: dates.append(dt)
    for m in GENERIC_STARTDATE_RE.finditer(html):
        dt = parse_iso_like(m.group(1)); 
        if dt: dates.append(dt)
    if dates: return dates, title
    # Visible text like "Oct 24, 2025 | 7:00 PM"
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    for m in DATE_TEXT_RE.finditer(text):
        mon_name = m.group(1); day = int(m.group(2)); year = m.group(3); h = int(m.group(4)); mm = int(m.group(5) or 0); ap = m.group(6).lower()
        mon = MONTH_MAP.get(mon_name); 
        if not mon: continue
        if ap=="pm" and h!=12: h+=12
        if ap=="am" and h==12: h=0
        y = int(year) if year else now_ny().year
        dates.append(datetime(y, mon, day, h, mm, tzinfo=SITE_TZ))
    return dates, title

def collect_event_links_from_location(html: str, base_url: str):
    links = set()
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/"):
            href = urljoin(base_url, href)
        if EVENT_HREF_RE.match(href):
            links.add(href.split("?")[0].rstrip("/") + "/")
    # Also try JSON "url" fields
    for m in re.finditer(r'"url"\s*:\s*"([^"]+)"', html):
        href = m.group(1)
        if href.startswith("/"):
            href = urljoin(base_url, href)
        if EVENT_HREF_RE.match(href):
            links.add(href.split("?")[0].rstrip("/") + "/")
    return sorted(links)

def build_ics(events):
    lines = ["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//asm-venue-ics v5//EN","CALSCALE:GREGORIAN","METHOD:PUBLISH","X-WR-CALNAME:ASM Syracuse — Venue Feed","X-WR-TIMEZONE:America/New_York"]
    for e in events: lines.append(e.to_ics())
    lines.append("END:VCALENDAR")
    return "\n".join(lines) + "\n"

def main():
    start_url = sys.argv[1] if len(sys.argv)>1 else DEFAULT_URL
    html = fetch(start_url)
    event_urls = collect_event_links_from_location(html, start_url)
    events = []
    if not event_urls:
        ds, title = find_dates_and_title_from_html(html)
        for d in ds:
            if d >= now_ny() - timedelta(days=1):
                events.append(Event(title or "ASM Syracuse Event", d, url=start_url))
    for u in event_urls:
        try:
            eh = fetch(u)
        except Exception:
            continue
        ds, title = find_dates_and_title_from_html(eh)
        for d in ds:
            if d >= now_ny() - timedelta(days=1):
                events.append(Event(title or "ASM Syracuse Event", d, url=u))
    if not events:
        s = now_ny() + timedelta(days=1, hours=9)
        events.append(Event("ASM Venue Feed Connected — awaiting events", s, s+timedelta(hours=1), url=start_url))
    events.sort(key=lambda e: e.start)
    with open("asm_calendar.ics", "w", encoding="utf-8") as f:
        f.write(build_ics(events))
    print(f"Wrote asm_calendar.ics with {len(events)} events from {len(event_urls)} URLs.")

if __name__ == "__main__":
    main()

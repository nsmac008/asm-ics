#!/usr/bin/env python3
import re
import sys
import uuid
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dateutil import tz

LIST_URL = "https://www.asmsyracuse.com/events"
SITE_TZ = tz.gettz("America/New_York")
DEFAULT_EVENT_DURATION_HOURS = 2
HEADERS = {"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"}

EVENT_HREF_RE = re.compile(r"https?://(?:www\.)?asmsyracuse\.com/(?:event|events)/[a-z0-9\-]+/?", re.I)
JSON_LD_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I|re.S)
ITEMPROP_STARTDATE_RE = re.compile(r'itemprop=["\']startDate["\'][^>]*?(?:datetime=["\']([^"\']+)["\']|content=["\']([^"\']+)["\'])', re.I)
TIME_TAG_RE = re.compile(r'<time[^>]+datetime=["\']([^"\']+)["\']', re.I)
GENERIC_STARTDATE_RE = re.compile(r'"startDate"\s*:\s*["\']([^"\']+)["\']')

def _now_ny():
    return datetime.now(tz=SITE_TZ)

def escape_ics(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

def to_utc_stamp(d: datetime) -> str:
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
            f"DTSTAMP:{to_utc_stamp(_now_ny())}",
            f"DTSTART:{to_utc_stamp(self.start)}",
            f"DTEND:{to_utc_stamp(self.end)}",
            f"SUMMARY:{escape_ics(self.title)}",
        ]
        if self.url:
            lines.append(f"URL:{escape_ics(self.url)}")
        lines.append("END:VEVENT")
        return "\n".join(lines)

def fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text

def collect_event_urls(list_html: str) -> list[str]:
    urls = set()
    # 1) direct hrefs
    for a in BeautifulSoup(list_html, "html.parser").find_all("a", href=True):
        href = a["href"]
        if href.startswith("/"):
            href = urljoin(LIST_URL, href)
        if EVENT_HREF_RE.match(href):
            urls.add(href.split("?")[0].rstrip("/")+"/")
    # 2) any strings matching the pattern (in case of JS blocks)
    for m in EVENT_HREF_RE.finditer(list_html):
        urls.add(m.group(0).split("?")[0].rstrip("/")+"/")
    return sorted(urls)

def parse_json_ld_blocks(html: str) -> list[dict]:
    out = []
    for m in JSON_LD_RE.finditer(html):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
            out.append(data)
        except Exception:
            # Sometimes multiple JSON objects are jammed together; try line by line
            for piece in re.split(r"</?script[^>]*>", raw):
                piece = piece.strip()
                if not piece:
                    continue
                try:
                    out.append(json.loads(piece))
                except Exception:
                    pass
    return out

def find_event_datetimes(html: str) -> tuple[list[datetime], str|None]:
    # returns ([start datetimes], title)
    # 1) JSON-LD
    dates = []
    title = None
    for block in parse_json_ld_blocks(html):
        for node in _walk(block):
            t = str(node.get("@type",""))
            if "Event" in t:
                if not title and isinstance(node.get("name"), str):
                    title = node["name"]
                iso = node.get("startDate") or node.get("start") or node.get("startTime")
                dt = _parse_iso_like(iso)
                if dt:
                    dates.append(dt)
    if dates:
        return dates, title

    # 2) Microdata itemprop="startDate"
    for m in ITEMPROP_STARTDATE_RE.finditer(html):
        iso = m.group(1) or m.group(2)
        dt = _parse_iso_like(iso)
        if dt:
            dates.append(dt)
    if dates:
        return dates, title

    # 3) <time datetime="...">
    for m in TIME_TAG_RE.finditer(html):
        dt = _parse_iso_like(m.group(1))
        if dt:
            dates.append(dt)
    if dates:
        return dates, title

    # 4) generic JSON "startDate":"..."
    for m in GENERIC_STARTDATE_RE.finditer(html):
        dt = _parse_iso_like(m.group(1))
        if dt:
            dates.append(dt)
    return dates, title

def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for x in node:
            yield from _walk(x)

def _parse_iso_like(iso: str|None) -> datetime|None:
    if not iso:
        return None
    s = iso.strip().replace(" ", "T")
    # Add default time if date-only
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        s += "T19:00:00"
    try:
        # Let Python parse timezone if present
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    # If naive, assume SITE_TZ
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SITE_TZ)
    return dt

def build_ics(events: list[Event]) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//asm-syracuse-ics v2//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:ASM Syracuse",
        "X-WR-TIMEZONE:America/New_York",
    ]
    for ev in events:
        lines.append(ev.to_ics())
    lines.append("END:VCALENDAR")
    return "\n".join(lines) + "\n"

def main():
    list_html = fetch(LIST_URL)
    event_urls = collect_event_urls(list_html)
    if not event_urls:
        print("No event URLs found on list page", file=sys.stderr)
        sys.exit(1)

    events = []
    for url in event_urls:
        try:
            html = fetch(url)
        except Exception as e:
            print(f"Fetch failed: {url} :: {e}", file=sys.stderr)
            continue
        dates, title = find_event_datetimes(html)
        if not dates:
            print(f"No dates on page: {url}", file=sys.stderr)
            continue
        title = title or _extract_title(html) or "ASM Syracuse Event"
        for dt in dates:
            if not isinstance(dt, datetime):
                continue
            if dt < _now_ny() - timedelta(days=1):
                continue
            events.append(Event(title, dt, url=url))

    if not events:
        print("No events parsed", file=sys.stderr)
        sys.exit(1)

    # sort & write
    events.sort(key=lambda e: e.start)
    ics = build_ics(events)
    with open("asm_calendar.ics", "w", encoding="utf-8") as f:
        f.write(ics)
    print(f"Wrote asm_calendar.ics with {len(events)} events from {len(event_urls)} pages")

def _extract_title(html: str) -> str|None:
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.I|re.S)
    if m:
        txt = re.sub(r"<[^>]+>", "", m.group(1))
        return " ".join(txt.split())
    t = re.search(r"<title>(.*?)</title>", html, re.I|re.S)
    if t:
        txt = re.sub(r"<[^>]+>", "", t.group(1))
        return " ".join(txt.split()).split("|")[0].strip()
    return None

if __name__ == "__main__":
    main()

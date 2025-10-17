#!/usr/bin/env python3
import re
import sys
import uuid
from datetime import datetime, timedelta
from dateutil import tz

import requests
from bs4 import BeautifulSoup

LIST_URL = "https://www.asmsyracuse.com/events"
SITE_TZ = tz.gettz("America/New_York")
DEFAULT_EVENT_DURATION_HOURS = 2

DOW = "(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*"
MONTHS = {
    "January":1,"February":2,"March":3,"April":4,"May":5,"June":6,
    "July":7,"August":8,"September":9,"October":10,"November":11,"December":12,
    "Jan.":1,"Feb.":2,"Mar.":3,"Apr.":4,"Jun.":6,"Jul.":7,"Aug.":8,"Sept.":9,"Oct.":10,"Nov.":11,"Dec.":12,
    "Jan":1,"Feb":2,"Mar":3,"Apr":4,"Jun":6,"Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12
}
DATE_HEADER = re.compile(rf"^(?P<dow>{DOW}),\s+(?P<mon>[A-Za-z]{{3,9}}\.?)\s+(?P<day>\d{{1,2}})$")
TIME_LINE = re.compile(r"^(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ampm>[APap][mM])$")

def escape_ics(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

def write_ics(events, path="asm_calendar.ics"):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//asm-syracuse-ics//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:ASM Syracuse",
        "X-WR-TIMEZONE:America/New_York",
    ]
    for e in events:
        lines.extend(e.to_ics_lines())
    lines.append("END:VCALENDAR")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

class Event:
    def __init__(self, title, start_dt, end_dt=None, url=None):
        self.title = title.strip()
        self.start = start_dt
        self.end = end_dt or (start_dt + timedelta(hours=DEFAULT_EVENT_DURATION_HOURS))
        self.url = url
        self.uid = f"{uuid.uuid4()}@asm-syracuse.com"
    def to_ics_lines(self):
        dtstamp = datetime.now(tz=tz.UTC).strftime("%Y%m%dT%H%M%SZ")
        dtstart = self.start.astimezone(tz.UTC).strftime("%Y%m%dT%H%M%SZ")
        dtend = self.end.astimezone(tz.UTC).strftime("%Y%m%dT%H%M%SZ")
        lines = [
            "BEGIN:VEVENT",
            f"UID:{self.uid}",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART:{dtstart}",
            f"DTEND:{dtend}",
            f"SUMMARY:{escape_ics(self.title)}",
        ]
        if self.url:
            lines.append(f"URL:{escape_ics(self.url)}")
        lines.append("END:VEVENT")
        return lines

def resolve_year_sequence(month_numbers):
    now = datetime.now(tz=SITE_TZ)
    year = now.year
    years = []
    prev = None
    for m in month_numbers:
        if prev is not None and m < prev:
            year += 1
        years.append(year)
        prev = m
    return years

def parse_events():
    html = requests.get(LIST_URL, headers={"User-Agent":"Mozilla/5.0"}, timeout=30).text
    soup = BeautifulSoup(html, "html.parser")

    lines = [s.strip() for s in soup.stripped_strings if s.strip()]

    headers = []
    for i, s in enumerate(lines):
        if DATE_HEADER.match(s):
            headers.append((i, s))
    if not headers:
        print("No date headers found", file=sys.stderr)
        return []

    month_numbers = []
    hdr_info = []
    for idx, text in headers:
        m = DATE_HEADER.match(text)
        mon_name = m.group("mon")
        mon = MONTHS.get(mon_name, MONTHS.get(mon_name.replace(".", ""), None))
        day = int(m.group("day"))
        month_numbers.append(mon)
        hdr_info.append((idx, mon, day))

    years = resolve_year_sequence(month_numbers)
    events = []
    for (hdr_idx, mon, day), year in zip(hdr_info, years):
        end_idx = next((i for i, _ in headers if i > hdr_idx), len(lines))
        i = hdr_idx + 1
        pending_time = None
        while i < end_idx:
            s = lines[i]
            t = TIME_LINE.match(s)
            if t:
                h = int(t.group("h"))
                m = int(t.group("m") or 0)
                ap = t.group("ampm").lower()
                if ap == "pm" and h != 12:
                    h += 12
                if ap == "am" and h == 12:
                    h = 0
                pending_time = (h, m)
                i += 1
                continue
            if pending_time:
                title = s
                # Try to find a matching anchor for URL
                url = None
                a = soup.find("a", string=lambda x: x and x.strip() == title)
                if a and a.get("href"):
                    url = a["href"]
                    if url.startswith("/"):
                        url = "https://www.asmsyracuse.com" + url
                dt_local = datetime(year, mon, day, pending_time[0], pending_time[1], tzinfo=SITE_TZ)
                events.append(Event(title, dt_local, url=url))
                pending_time = None
            i += 1

    now = datetime.now(tz=SITE_TZ) - timedelta(days=1)
    events = [e for e in events if e.start > now]
    events.sort(key=lambda e: e.start)
    return events

def main():
    events = parse_events()
    if not events:
        print("No events parsed", file=sys.stderr)
        sys.exit(1)
    write_ics(events, path="asm_calendar.ics")
    print(f"Wrote asm_calendar.ics with {len(events)} events")

if __name__ == "__main__":
    main()

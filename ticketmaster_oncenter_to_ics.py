#!/usr/bin/env python3
"""
Ticketmaster → ICS for The Oncenter Crouse Hinds Theater
Writes: public/asm_oncenter.ics
Prefix: "Oncenter: "
"""
import os, re, sys, json, uuid
from datetime import datetime, timedelta, timezone
from typing import List, Tuple, Any
import requests
from bs4 import BeautifulSoup
from dateutil import tz

VENUE_URL = "https://www.ticketmaster.com/the-oncenter-crouse-hinds-theater-tickets-syracuse/venue/184"
SITE_TZ = tz.gettz("America/New_York")
TITLE_PREFIX = "Oncenter: "
DEFAULT_EVENT_DURATION_HOURS = 2
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
OUT_DIR = "public"
OUT_FILE = os.path.join(OUT_DIR, "asm_oncenter.ics")

def now_ny(): return datetime.now(tz=SITE_TZ)

def fetch_html(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text

def parse_json_blocks(html: str):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for tag in soup.find_all("script", {"type": "application/ld+json"}):
        text = tag.string or tag.text or ""
        if not text.strip(): continue
        try:
            out.append(json.loads(text))
        except Exception:
            try:
                fixed = "[" + re.sub(r"}\s*{\s*", "},{", text.strip().strip(";")) + "]"
                out.append(json.loads(fixed))
            except Exception:
                pass
    return out

def walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values(): yield from walk(v)
    elif isinstance(node, list):
        for x in node: yield from walk(x)

def parse_iso_guess_local(s: str):
    if not s: return None
    s = s.strip().replace(" ", "T")
    try:
        if s.endswith("Z"):
            return datetime.fromisoformat(s.replace("Z","+00:00")).astimezone(SITE_TZ)
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None: dt = dt.replace(tzinfo=SITE_TZ)
        return dt
    except Exception:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            y, mo, d = map(int, s.split("-"))
            return datetime(y, mo, d, 19, 0, tzinfo=SITE_TZ)
    return None

def collect_events_from_jsonld(html: str):
    blocks = parse_json_blocks(html)
    events = []
    for block in blocks:
        for node in walk(block):
            t = node.get("@type")
            if not t: continue
            is_event = any("Event" in x for x in t) if isinstance(t, list) else ("Event" in str(t))
            if not is_event: continue
            title = None
            for key in ("name","headline"):
                v = node.get(key)
                if isinstance(v,str) and v.strip():
                    title = v.strip(); break
            start_raw = node.get("startDate") or node.get("startTime") or node.get("start")
            start_dt = parse_iso_guess_local(start_raw) if isinstance(start_raw,str) else None
            url = None
            for key in ("url","mainEntityOfPage"):
                v = node.get(key)
                if isinstance(v,str) and v.startswith("http"): url = v; break
                if isinstance(v,dict) and isinstance(v.get("@id"),str) and v["@id"].startswith("http"):
                    url = v["@id"]; break
            desc = node.get("description") if isinstance(node.get("description"), str) else ""
            if title and start_dt: events.append((title,start_dt,url or "",desc or ""))
    seen, uniq = set(), []
    for t, s, u, d in events:
        key = (t, s.isoformat(), u)
        if key in seen: continue
        seen.add(key); uniq.append((t,s,u,d))
    return uniq

def escape_ics(text: str) -> str:
    return (text or "").replace("\\","\\\\").replace(";","\\;").replace(",","\\,").replace("\n","\\n")

def to_ics(events):
    lines = [
        "BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//ticketmaster-venue-ics//EN",
        "CALSCALE:GREGORIAN","METHOD:PUBLISH",
        "X-WR-CALNAME:The Oncenter Crouse Hinds Theater — Ticketmaster",
        "X-WR-TIMEZONE:America/New_York",
    ]
    for title, start_dt, url, desc in sorted(events, key=lambda x: x[1]):
        uid = f"{uuid.uuid4()}@ticketmaster.com"
        dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dtstart = start_dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dtend = (start_dt + timedelta(hours=DEFAULT_EVENT_DURATION_HOURS)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART:{dtstart}",
            f"DTEND:{dtend}",
            f"SUMMARY:{escape_ics(TITLE_PREFIX + title)}",
        ]
        if url:  lines.append(f"URL:{escape_ics(url)}")
        if desc: lines.append(f"DESCRIPTION:{escape_ics(desc)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\n".join(lines) + "\n"

def main():
    html = fetch_html(VENUE_URL)
    items = collect_events_from_jsonld(html)
    cutoff = now_ny() - timedelta(days=1)
    items = [(t,s,u,d) for (t,s,u,d) in items if s >= cutoff]
    if not items:
        placeholder = now_ny() + timedelta(days=1, hours=9)
        items = [("Venue Feed Connected — awaiting events", placeholder, VENUE_URL, "No upcoming events yet.")]
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(to_ics(items))
    print(f"Wrote {OUT_FILE} with {len(items)} events")

if __name__ == "__main__":
    main()

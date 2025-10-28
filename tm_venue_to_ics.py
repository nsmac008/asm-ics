#!/usr/bin/env python3
import re
import sys
import uuid
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from dateutil import parser as dtparse
from dateutil import tz

# ------------
# CONFIG
# ------------
SITE_TZ = tz.gettz("America/New_York")
DEFAULT_EVENT_DURATION_HOURS = 2
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.ticketmaster.com/",
}

VENUES = [
    {
        "url": "https://www.ticketmaster.com/upstate-medical-university-arena-at-the-tickets-syracuse/venue/186",
        "outfile": "public/asm_warmemorial.ics",
        "prefix": "War Memorial: ",
    },
    {
        "url": "https://www.ticketmaster.com/the-oncenter-crouse-hinds-theater-tickets-syracuse/venue/184",
        "outfile": "public/oncenter_crousehinds.ics",
        "prefix": "Oncenter: ",
    },
]


# ------------
# HELPERS
# ------------
def escape_ics(text: str) -> str:
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def fetch_html(url):
    resp = requests.get(url, headers=HEADERS, timeout=25)
    resp.raise_for_status()
    return resp.text


def parse_ticketmaster_events(html, prefix):
    soup = BeautifulSoup(html, "html.parser")
    events = []

    cards = soup.select("div.event-listing, li.event-listing, a.event")
    if not cards:
        cards = soup.select("a[href*='/event/']")

    for card in cards:
        title_el = card.find(["h3", "h2"]) or card
        title = title_el.get_text(strip=True) if title_el else None
        if not title:
            continue
        title = f"{prefix}{title}"

        link = None
        a = card.find("a", href=True)
        if a and "/event/" in a["href"]:
            link = a["href"]
            if link.startswith("/"):
                link = f"https://www.ticketmaster.com{link}"

        date_el = card.find(["time", "span"], class_=re.compile("date", re.I))
        date_txt = date_el.get_text(strip=True) if date_el else None
        time_el = card.find(["span"], class_=re.compile("time", re.I))
        time_txt = time_el.get_text(strip=True) if time_el else ""

        if not date_txt:
            continue

        dt = parse_date_time(date_txt, time_txt)
        if not dt:
            continue

        end_dt = dt + timedelta(hours=DEFAULT_EVENT_DURATION_HOURS)
        events.append(
            {
                "title": title,
                "start": dt,
                "end": end_dt,
                "url": link,
            }
        )

    return events


def parse_date_time(date_str, time_str):
    try:
        base = dtparse.parse(date_str)
        t = dtparse.parse(time_str) if time_str else None
        if t:
            base = base.replace(hour=t.hour, minute=t.minute)
        return base.replace(tzinfo=SITE_TZ)
    except Exception:
        return None


def write_ics(events, path, venue_name):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//asm-ics//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{venue_name}",
        "X-WR-TIMEZONE:America/New_York",
    ]

    for e in sorted(events, key=lambda x: x["start"]):
        uid = f"{uuid.uuid4()}@asm-ics"
        dtstamp = datetime.now(tz=tz.UTC).strftime("%Y%m%dT%H%M%SZ")
        dtstart = e["start"].astimezone(tz.UTC).strftime("%Y%m%dT%H%M%SZ")
        dtend = e["end"].astimezone(tz.UTC).strftime("%Y%m%dT%H%M%SZ")

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART:{dtstart}",
            f"DTEND:{dtend}",
            f"SUMMARY:{escape_ics(e['title'])}",
        ])
        if e["url"]:
            lines.append(f"URL:{escape_ics(e['url'])}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote {path} with {len(events)} events")


# ------------
# MAIN
# ------------
def main():
    for v in VENUES:
        print(f"Fetching: {v['url']}")
        try:
            html = fetch_html(v["url"])
            events = parse_ticketmaster_events(html, v["prefix"])
            if not events:
                print(f"No events parsed for {v['url']}")
                continue
            write_ics(events, v["outfile"], v["prefix"].strip(": "))
        except Exception as e:
            print(f"Error parsing {v['url']}: {e}")


if __name__ == "__main__":
    main()

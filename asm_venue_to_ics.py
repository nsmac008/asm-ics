#!/usr/bin/env python3
"""
ASM Syracuse (venue/location page) → auto-updating ICS feed
v6 — includes robust title extraction and multiple date parsing fallbacks.

Usage (local):
  python asm_venue_to_ics.py [optional-venue-url]

Default venue URL (can be overridden by CLI arg):
  https://www.asmsyracuse.com/location/upstate-medical-arena-at-the-oncenter-war-memorial?ev=690&th=fairgrounds

Outputs:
  asm_calendar.ics in the current directory
"""

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
    )
}
DEFAULT_URL = (
    "https://www.asmsyracuse.com/location/"
    "upstate-medical-arena-at-the-oncenter-war-memorial?ev=690&th=fairgrounds"
)

# Recognize event detail links (event pages under /events/... or /event/...)
EVENT_HREF_RE = re.compile(
    r"https?://(?:www\.)?asmsyracuse\.com/(?:events|event)/[a-z0-9\-/]+/?",
    re.I,
)

# JSON-LD, microdata, meta, and generic patterns
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

# Robust visible-text detector: "Oct 24, 2025 | 7:00 PM" / "Oct 24 • 7:00 PM" / "October 24 at 7:00 PM"
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

# ── Helpers ────────────────────────────────────────────────────────────────────

def now_ny() -> datetime:
    return datetime.now(tz=SITE_TZ)

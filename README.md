# ASM Syracuse → auto-updating `.ics` feed

Mirrors https://www.asmsyracuse.com/events into a public iCalendar feed.

**Subscribe URL (after first deploy):**
```
https://<your-username>.github.io/asm-ics/asm_calendar.ics
```

## How to use
1. Create a new repo (e.g. `asm-ics`) and upload:
   - `asm_to_ics.py` (the scraper)
   - `requirements.txt`
   - `.github/workflows/publish.yml` (GitHub Actions workflow)
2. Settings → **Pages** → Source: **GitHub Actions**.
3. Actions → **Build & Publish ASM ICS** → Run workflow.
4. Subscribe to the resulting `asm_calendar.ics` in Google/Apple/Outlook.

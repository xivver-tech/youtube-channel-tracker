# YouTube Channel Tracker

A **full local app** to track YouTube channels — no Google API key required.

## What it does
- Add channels by URL, `@handle`, or channel ID
- Sync latest videos via **official YouTube RSS feeds**
- Optional enrichment with **yt-dlp** (duration, view counts)
- SQLite database (searchable, persistent)
- Search across titles & descriptions
- Stats per channel
- Export to JSON or CSV

## Requirements
- Python 3.8+
- Optional: `pip install yt-dlp` for `--enrich`

## Usage
```bash
# Track a channel
python yttracker.py add https://www.youtube.com/@veritasium
python yttracker.py add UCxxxxxxxxxxxxxxxxxxxxxx

# Pull latest videos
python yttracker.py sync
python yttracker.py sync --enrich    # also fetch duration/views via yt-dlp

# Browse
python yttracker.py channels
python yttracker.py videos 30
python yttracker.py videos 20 --channel veritasium
python yttracker.py search "black hole"
python yttracker.py stats

# Export
python yttracker.py export
python yttracker.py export --csv my_videos.csv

# Remove a channel
python yttracker.py remove veritasium
```

Data is stored in `yttracker.db` next to the script.

## How it works
YouTube publishes a public RSS feed per channel:
`https://www.youtube.com/feeds/videos.xml?channel_id=CHANNEL_ID`

This app resolves handles → channel IDs, pulls that feed, and stores everything locally. No scraping of HTML video pages required for the core workflow.

#!/usr/bin/env python3
"""
YouTube Channel Tracker
=======================
A complete local app to track YouTube channels without an API key.

Features:
  - Add channels by URL or channel ID
  - Sync latest videos via official YouTube RSS feeds
  - Optional enrichment with yt-dlp (if installed)
  - SQLite database
  - Search videos / channels
  - Stats and reports
  - Export to CSV / JSON

No Google API key required. Uses public channel RSS feeds.
"""

import csv
import json
import re
import sqlite3
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs

DB_PATH = Path(__file__).parent / "yttracker.db"
ATOM_NS = {"yt": "http://www.youtube.com/xml/schemas/2015", "atom": "http://www.w3.org/2005/Atom", "media": "http://search.yahoo.com/mrss/"}

# ---------- database ----------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS channels (
        id TEXT PRIMARY KEY,
        title TEXT,
        url TEXT,
        added_at TEXT,
        last_sync TEXT
    );
    CREATE TABLE IF NOT EXISTS videos (
        id TEXT PRIMARY KEY,
        channel_id TEXT,
        title TEXT,
        published TEXT,
        url TEXT,
        description TEXT,
        thumbnail TEXT,
        duration TEXT,
        view_count INTEGER,
        FOREIGN KEY(channel_id) REFERENCES channels(id)
    );
    CREATE INDEX IF NOT EXISTS idx_videos_channel ON videos(channel_id);
    CREATE INDEX IF NOT EXISTS idx_videos_published ON videos(published);
    """)
    return conn

# ---------- helpers ----------

def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": "yttracker/1.0 (local channel tracker)"
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()

def extract_channel_id(url_or_id):
    """Accept channel ID, /channel/URL, /@handle, or /c/name and resolve to channel ID."""
    s = url_or_id.strip()

    # Already a channel ID (UC...)
    if re.fullmatch(r"UC[\w-]{22}", s):
        return s

    # channel URL
    m = re.search(r"youtube\.com/channel/(UC[\w-]{22})", s)
    if m:
        return m.group(1)

    # For @handle or /c/ we need to fetch the page and find channelId
    if "youtube.com" in s or s.startswith("@"):
        if s.startswith("@"):
            s = "https://www.youtube.com/" + s
        elif not s.startswith("http"):
            s = "https://www.youtube.com/" + s.lstrip("/")
        try:
            html = http_get(s).decode("utf-8", errors="ignore")
            # common patterns in YouTube HTML
            for pattern in [
                r'"channelId":"(UC[\w-]{22})"',
                r'channel_id=(UC[\w-]{22})',
                r'/channel/(UC[\w-]{22})',
            ]:
                m = re.search(pattern, html)
                if m:
                    return m.group(1)
        except Exception as e:
            print(f"  Could not resolve channel from page: {e}")

    return None

def rss_url(channel_id):
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

def parse_rss(channel_id):
    """Fetch and parse channel RSS. Returns (channel_title, list of video dicts)."""
    data = http_get(rss_url(channel_id))
    root = ET.fromstring(data)

    title_el = root.find("atom:title", ATOM_NS)
    channel_title = title_el.text if title_el is not None else channel_id

    videos = []
    for entry in root.findall("atom:entry", ATOM_NS):
        vid = entry.find("yt:videoId", ATOM_NS)
        vtitle = entry.find("atom:title", ATOM_NS)
        published = entry.find("atom:published", ATOM_NS)
        link = entry.find("atom:link", ATOM_NS)
        media = entry.find("media:group", ATOM_NS)

        description = ""
        thumbnail = ""
        if media is not None:
            desc = media.find("media:description", ATOM_NS)
            if desc is not None and desc.text:
                description = desc.text[:2000]
            thumb = media.find("media:thumbnail", ATOM_NS)
            if thumb is not None:
                thumbnail = thumb.get("url", "")

        if vid is None:
            continue

        videos.append({
            "id": vid.text,
            "channel_id": channel_id,
            "title": vtitle.text if vtitle is not None else "",
            "published": published.text if published is not None else "",
            "url": link.get("href") if link is not None else f"https://youtu.be/{vid.text}",
            "description": description,
            "thumbnail": thumbnail,
            "duration": None,
            "view_count": None,
        })
    return channel_title, videos

def enrich_with_ytdlp(video_id):
    """Optional: use yt-dlp for duration / view count if installed."""
    try:
        import subprocess
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--skip-download", f"https://youtu.be/{video_id}"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return {}
        info = json.loads(result.stdout)
        return {
            "duration": str(info.get("duration") or ""),
            "view_count": info.get("view_count"),
            "description": (info.get("description") or "")[:2000],
        }
    except FileNotFoundError:
        return {}
    except Exception:
        return {}

# ---------- commands ----------

def cmd_add(url_or_id):
    channel_id = extract_channel_id(url_or_id)
    if not channel_id:
        print("Could not resolve channel ID. Try a full channel URL or UC... ID.")
        return

    print(f"Resolved channel ID: {channel_id}")
    try:
        title, videos = parse_rss(channel_id)
    except Exception as e:
        print(f"Failed to fetch RSS: {e}")
        return

    conn = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT OR REPLACE INTO channels (id, title, url, added_at, last_sync) VALUES (?,?,?,?,?)",
        (channel_id, title, f"https://www.youtube.com/channel/{channel_id}", now, now)
    )

    added = 0
    for v in videos:
        cur = conn.execute(
            "INSERT OR IGNORE INTO videos (id, channel_id, title, published, url, description, thumbnail, duration, view_count) VALUES (?,?,?,?,?,?,?,?,?)",
            (v["id"], v["channel_id"], v["title"], v["published"], v["url"], v["description"], v["thumbnail"], v["duration"], v["view_count"])
        )
        if cur.rowcount:
            added += 1
    conn.commit()
    conn.close()
    print(f"✓ Added channel: {title}")
    print(f"  {added} new videos indexed ({len(videos)} in feed)")

def cmd_sync(enrich=False):
    conn = get_db()
    channels = conn.execute("SELECT id, title FROM channels").fetchall()
    if not channels:
        print("No channels. Add one first: python yttracker.py add <url>")
        conn.close()
        return

    total_new = 0
    for ch in channels:
        print(f"Syncing: {ch['title']} ...")
        try:
            title, videos = parse_rss(ch["id"])
        except Exception as e:
            print(f"  Error: {e}")
            continue

        new_count = 0
        for v in videos:
            if enrich:
                extra = enrich_with_ytdlp(v["id"])
                v.update({k: extra[k] for k in extra if extra.get(k) is not None})

            cur = conn.execute(
                "INSERT OR IGNORE INTO videos (id, channel_id, title, published, url, description, thumbnail, duration, view_count) VALUES (?,?,?,?,?,?,?,?,?)",
                (v["id"], v["channel_id"], v["title"], v["published"], v["url"], v["description"], v["thumbnail"], v.get("duration"), v.get("view_count"))
            )
            if cur.rowcount:
                new_count += 1
                # update if we enriched an existing row later — for new ones data is already set

            if enrich and v.get("duration") or v.get("view_count"):
                conn.execute(
                    "UPDATE videos SET duration=COALESCE(?, duration), view_count=COALESCE(?, view_count), description=COALESCE(?, description) WHERE id=?",
                    (v.get("duration"), v.get("view_count"), v.get("description"), v["id"])
                )

        conn.execute("UPDATE channels SET last_sync=?, title=? WHERE id=?",
                     (datetime.now().isoformat(timespec="seconds"), title, ch["id"]))
        print(f"  +{new_count} new")
        total_new += new_count

    conn.commit()
    conn.close()
    print(f"\n✓ Sync complete. {total_new} new videos total.")

def cmd_list_channels():
    conn = get_db()
    rows = conn.execute("""
        SELECT c.id, c.title, c.last_sync, COUNT(v.id) as video_count
        FROM channels c
        LEFT JOIN videos v ON v.channel_id = c.id
        GROUP BY c.id
        ORDER BY c.title
    """).fetchall()
    conn.close()
    if not rows:
        print("No channels.")
        return
    print(f"{'Title':<40} {'Videos':>7}  Last sync")
    print("-" * 70)
    for r in rows:
        print(f"{r['title'][:40]:<40} {r['video_count']:>7}  {(r['last_sync'] or '-')[:19]}")

def cmd_list_videos(limit=20, channel=None):
    conn = get_db()
    if channel:
        rows = conn.execute("""
            SELECT v.title, v.published, v.url, c.title as channel
            FROM videos v JOIN channels c ON c.id = v.channel_id
            WHERE c.title LIKE ? OR v.channel_id = ?
            ORDER BY v.published DESC LIMIT ?
        "", (f"%{channel}%", channel, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT v.title, v.published, v.url, c.title as channel
            FROM videos v JOIN channels c ON c.id = v.channel_id
            ORDER BY v.published DESC LIMIT ?
        "", (limit,)).fetchall()
    conn.close()

    if not rows:
        print("No videos.")
        return
    for r in rows:
        pub = (r["published"] or "")[:10]
        print(f"[{pub}] {r['channel']}")
        print(f"         {r['title']}")
        print(f"         {r['url']}\n")

def cmd_search(query, limit=20):
    conn = get_db()
    q = f"%{query}%"
    rows = conn.execute("""
        SELECT v.title, v.published, v.url, c.title as channel, v.description
        FROM videos v JOIN channels c ON c.id = v.channel_id
        WHERE v.title LIKE ? OR v.description LIKE ? OR c.title LIKE ?
        ORDER BY v.published DESC LIMIT ?
    "", (q, q, q, limit)).fetchall()
    conn.close()

    print(f"Found {len(rows)} result(s) for '{query}':\n")
    for r in rows:
        pub = (r["published"] or "")[:10]
        print(f"[{pub}] {r['channel']} — {r['title']}")
        print(f"         {r['url']}\n")

def cmd_stats():
    conn = get_db()
    channels = conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
    videos = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    print(f"Channels tracked : {channels}")
    print(f"Videos indexed   : {videos}")
    print()
    rows = conn.execute("""
        SELECT c.title, COUNT(v.id) as n
        FROM channels c LEFT JOIN videos v ON v.channel_id = c.id
        GROUP BY c.id ORDER BY n DESC
    """).fetchall()
    print("Videos per channel:")
    for r in rows:
        print(f"  {r['title']:<40} {r['n']:>5}")
    conn.close()

def cmd_export(fmt="json", path=None):
    conn = get_db()
    rows = conn.execute("""
        SELECT v.id, v.title, v.published, v.url, v.description,
               v.duration, v.view_count, c.title as channel, c.id as channel_id
        FROM videos v JOIN channels c ON c.id = v.channel_id
        ORDER BY v.published DESC
    """).fetchall()
    conn.close()

    data = [dict(r) for r in rows]
    if fmt == "csv":
        out = path or "videos.csv"
        with open(out, "w", newline="", encoding="utf-8") as f:
            if not data:
                print("Nothing to export.")
                return
            writer = csv.DictWriter(f, fieldnames=data[0].keys())
            writer.writeheader()
            writer.writerows(data)
        print(f"✓ Exported {len(data)} videos → {out}")
    else:
        out = path or "videos.json"
        Path(out).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"✓ Exported {len(data)} videos → {out}")

def cmd_remove(channel_query):
    conn = get_db()
    rows = conn.execute(
        "SELECT id, title FROM channels WHERE title LIKE ? OR id = ?",
        (f"%{channel_query}%", channel_query)
    ).fetchall()
    if not rows:
        print("Channel not found.")
        conn.close()
        return
    if len(rows) > 1:
        print("Multiple matches, be more specific:")
        for r in rows:
            print(f"  {r['id']}  {r['title']}")
        conn.close()
        return
    ch = rows[0]
    conn.execute("DELETE FROM videos WHERE channel_id = ?", (ch["id"],))
    conn.execute("DELETE FROM channels WHERE id = ?", (ch["id"],))
    conn.commit()
    conn.close()
    print(f"✓ Removed channel: {ch['title']}")

def main():
    if len(sys.argv) < 2:
        print("""YouTube Channel Tracker
=======================
Full local app — no API key needed (uses public RSS).

  add <url|@handle|channel_id>   Track a channel
  sync [--enrich]                Fetch latest videos (optional yt-dlp)
  channels                       List tracked channels
  videos [n] [--channel name]    List recent videos
  search <query>                 Search titles/descriptions
  stats                          Overview numbers
  export [--csv] [file]          Export database
  remove <name|id>               Stop tracking a channel

Examples:
  python yttracker.py add https://www.youtube.com/@veritasium
  python yttracker.py add UCxxxxxxxxxxxxxxxxxxxxxx
  python yttracker.py sync
  python yttracker.py sync --enrich          # needs yt-dlp installed
  python yttracker.py videos 30
  python yttracker.py search "black hole"
  python yttracker.py export --csv

Optional: pip install yt-dlp   for duration/view counts on sync --enrich
""")
        return

    cmd = sys.argv[1].lower()

    if cmd == "add" and len(sys.argv) > 2:
        cmd_add(sys.argv[2])
    elif cmd == "sync":
        enrich = "--enrich" in sys.argv
        cmd_sync(enrich=enrich)
    elif cmd == "channels":
        cmd_list_channels()
    elif cmd == "videos":
        limit = 20
        channel = None
        args = sys.argv[2:]
        i = 0
        while i < len(args):
            if args[i] == "--channel" and i + 1 < len(args):
                channel = args[i + 1]; i += 2
            elif args[i].isdigit():
                limit = int(args[i]); i += 1
            else:
                i += 1
        cmd_list_videos(limit, channel)
    elif cmd == "search" and len(sys.argv) > 2:
        cmd_search(" ".join(sys.argv[2:]))
    elif cmd == "stats":
        cmd_stats()
    elif cmd == "export":
        fmt = "csv" if "--csv" in sys.argv else "json"
        path = None
        for a in sys.argv[2:]:
            if not a.startswith("--"):
                path = a
        cmd_export(fmt, path)
    elif cmd == "remove" and len(sys.argv) > 2:
        cmd_remove(" ".join(sys.argv[2:]))
    else:
        print("Unknown command. Run without args for help.")

if __name__ == "__main__":
    main()

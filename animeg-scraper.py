#!/usr/bin/env python3
"""
AnimeGG Scraper - GitHub Actions Edition
- Reads SCRAPER_START / SCRAPER_END from env (set by workflow)
- Auto-resumes via data/state.json when env vars not set
- Auto-splits JSON at 3 MB -> animeg_part_1.json, animeg_part_2.json ...
- Saves data/index.json listing all parts
- Stores series page URLs (not episode URLs); only unique URLs kept
- Persists processed URLs to data/already_processed_page_urls_list.json for fast dedup
"""

import re
import requests
from bs4 import BeautifulSoup
import json
import time
import os
import sys

# Constants
BASE_URL      = "https://www.animegg.org"
DATA_DIR      = "data"
STATE_FILE    = os.path.join(DATA_DIR, "state.json")
INDEX_FILE    = os.path.join(DATA_DIR, "index.json")
PROCESSED_FILE = os.path.join(DATA_DIR, "already_processed_page_urls_list.json")
MAX_PAGE      = 6500
MAX_FILE_SIZE = 3 * 1024 * 1024   # 3 MB
PAGES_PER_RUN = 1000              # default when no end given
DELAY         = 1.2               # seconds between requests

# Matches "-episode-5", "-episode-14", "-episode-5-5", etc. at end of slug
_EP_RE = re.compile(r"-episode(?:-[\d]+)+$", re.IGNORECASE)


def to_series_url(episode_href: str) -> str:
    """
    Convert an episode URL into its series page URL.

    https://www.animegg.org/the-cat-and-the-dragon-episode-5#subbed
      -> https://www.animegg.org/series/the-cat-and-the-dragon#episodes
    """
    url  = episode_href.split("#")[0].rstrip("/")   # drop #fragment
    slug = url.rsplit("/", 1)[-1]                   # last path segment
    slug = _EP_RE.sub("", slug)                     # strip -episode-N
    return f"{BASE_URL}/series/{slug}#episodes"


# State

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"next_page": 0, "part": 1, "total_urls": 0}

def save_state(state):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# Part files

def part_path(n):
    return os.path.join(DATA_DIR, f"animeg_part_{n}.json")

def load_part(n):
    p = part_path(n)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return []

def save_part(n, data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(part_path(n), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def part_size(n):
    p = part_path(n)
    return os.path.getsize(p) if os.path.exists(p) else 0

def estimated_size(data):
    return sum(
        len(e["series_title"].encode()) + len(e["series_href"].encode()) + 40
        for e in data
    )


# Index

def update_index(parts):
    entries = []
    for n in sorted(parts):
        p = part_path(n)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        entries.append({
            "part":         n,
            "file":         f"animeg_part_{n}.json",
            "entries":      len(data),
            "size_bytes":   os.path.getsize(p),
            "serial_range": [
                data[0]["serial_no"]  if data else None,
                data[-1]["serial_no"] if data else None,
            ],
        })
    with open(INDEX_FILE, "w") as f:
        json.dump(entries, f, indent=2)
    total = sum(e["entries"] for e in entries)
    print(f"[index] {len(entries)} parts | {total} total entries")


# Processed-URL registry
#
# data/already_processed_page_urls_list.json is a JSON array of every
# series_href that has already been saved.  It is the single source of
# truth for deduplication — no need to re-scan all part files on startup.

def load_processed_urls() -> set:
    """Load the persisted set of already-processed series URLs."""
    if os.path.exists(PROCESSED_FILE):
        with open(PROCESSED_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_processed_urls(seen: set) -> None:
    """Persist the full set of processed URLs to disk (sorted for readability)."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=2)


# HTTP

def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer":         BASE_URL,
    })
    try:
        s.get(BASE_URL, timeout=15)
    except Exception:
        pass
    return s

def fetch_page(session, page, retries=3):
    url = f"{BASE_URL}/releases?start={page * 10}"
    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 200:
                return r.text
            print(f"  HTTP {r.status_code} (attempt {attempt})")
        except Exception as e:
            print(f"  Error: {e} (attempt {attempt})")
        if attempt < retries:
            time.sleep(3 * attempt)
    return None


# Parse

def parse_episodes(html):
    """Return list of (series_title, series_href) — raw, not yet deduped."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    divs = soup.find_all(
        "div",
        class_=lambda c: c and "rightpop" in c and "release" in c
    )
    for div in divs:
        ul = div.find("ul", class_="tags")
        if not ul:
            continue
        for li in ul.find_all("li"):
            a = li.find("a", href=True)
            if not a:
                continue
            strong    = a.find("strong")
            raw_title = strong.get_text(strip=True) if strong else a.get_text(strip=True)
            series_href  = to_series_url(BASE_URL + a["href"])
            series_title = re.sub(
                r"\s+[Ee]pisode\s+[\d]+(?:[.\-][\d]+)*\s*$", "", raw_title
            ).strip()
            results.append((series_title, series_href))
    return results


# Main

def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    state     = load_state()
    env_start = os.environ.get("SCRAPER_START", "").strip()
    env_end   = os.environ.get("SCRAPER_END",   "").strip()

    start_page = int(env_start) if env_start else state.get("next_page", 0)
    end_page   = int(env_end)   if env_end   else start_page + PAGES_PER_RUN - 1

    start_page = max(0, min(start_page, MAX_PAGE))
    end_page   = max(start_page, min(end_page, MAX_PAGE))

    if start_page > MAX_PAGE:
        print(f"All pages up to {MAX_PAGE} already scraped.")
        sys.exit(0)

    serial_offset = state.get("total_urls", 0)
    current_part  = state.get("part", 1)

    print(f"\n{'='*58}")
    print(f"  AnimeGG Scraper")
    print(f"{'='*58}")
    print(f"  Page range  : {start_page} -> {end_page}  ({end_page - start_page + 1} pages)")
    print(f"  URL range   : ?start={start_page*10} -> ?start={end_page*10}")
    print(f"  Serial off  : {serial_offset}")
    print(f"  Current part: animeg_part_{current_part}.json")
    print(f"{'='*58}\n")

    seen_urls = load_processed_urls()
    print(f"[dedup] {len(seen_urls)} unique series URLs in processed list\n")

    session      = make_session()
    buffer       = load_part(current_part)
    parts_used   = {current_part}
    urls_scraped = 0
    dups_skipped = 0
    empty_streak = 0

    for page in range(start_page, end_page + 1):
        pct = ((page - start_page) / max(end_page - start_page, 1)) * 100
        print(f"[{pct:5.1f}%] page {page:>5} (?start={page*10:<6}) | ", end="", flush=True)

        html = fetch_page(session, page)

        if html is None:
            print("SKIPPED (fetch failed)")
            empty_streak += 1
        else:
            raw           = parse_episodes(html)
            new_this_page = 0

            for series_title, series_href in raw:
                if series_href in seen_urls:
                    dups_skipped += 1
                    continue
                seen_urls.add(series_href)
                serial_offset += 1
                urls_scraped  += 1
                new_this_page += 1
                buffer.append({
                    "serial_no":    serial_offset,
                    "series_title": series_title,
                    "series_href":  series_href,
                })

                if len(buffer) % 100 == 0:
                    if estimated_size(buffer) >= MAX_FILE_SIZE:
                        save_part(current_part, buffer)
                        size_kb = part_size(current_part) / 1024
                        print(f"\n  -> [SPLIT] Part {current_part} "
                              f"({size_kb:.0f} KB >= 3 MB) "
                              f"-> starting part {current_part + 1}")
                        current_part += 1
                        parts_used.add(current_part)
                        buffer = []

            if not raw:
                print("0 episodes")
                empty_streak += 1
                if empty_streak >= 5:
                    print("\n[stop] 5 consecutive empty/failed pages -- end of content.")
                    break
            else:
                empty_streak = 0
                print(f"{new_this_page:>3} new | "
                      f"{len(raw) - new_this_page} dup | "
                      f"serial up to {serial_offset}")

        save_part(current_part, buffer)
        parts_used.add(current_part)
        save_state({"next_page": page + 1, "part": current_part, "total_urls": serial_offset})
        save_processed_urls(seen_urls)

        if page < end_page:
            time.sleep(DELAY)

    save_part(current_part, buffer)
    update_index(parts_used)

    print(f"\n{'='*58}")
    print(f"  Done!")
    print(f"  Pages scraped   : {start_page} -> {end_page}")
    print(f"  New URLs        : {urls_scraped}")
    print(f"  Duplicates skip : {dups_skipped}")
    print(f"  Total unique    : {serial_offset}")
    print(f"  Parts written   : {sorted(parts_used)}")
    print(f"  Next run page   : {end_page + 1}")
    print(f"{'='*58}\n")


if __name__ == "__main__":
    main()

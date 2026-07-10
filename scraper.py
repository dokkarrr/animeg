#!/usr/bin/env python3
"""
scraper.py

Auto-generates episode URLs for configured anime series, scrapes each page
for its title and iframe srcs (sub/dub), and saves the results to JSON files.

Configuration is done entirely in the SERIES list below — no external txt
file needed. Each entry defines:
    slug        : URL slug used on animegg.org  (e.g. "naruto")
    total_eps   : total number of episodes
    mal_id      : MyAnimeList ID  (fill in manually or leave None)
    tmdb_id     : TMDB ID         (fill in manually or leave None)
    output_file : filename for the JSON output

Usage:
    python scraper.py

For GitHub Actions, see .github/workflows/scrape.yml
"""

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ── Series configuration ───────────────────────────────────────────────────────
# Add or edit entries here to scrape additional anime.
SERIES = [
    {
        "slug":        "naruto",
        "total_eps":   220,
        "mal_id":      20,       # https://myanimelist.net/anime/20
        "tmdb_id":     46260,    # https://www.themoviedb.org/tv/46260
        "output_file": "naruto.json",
    },
    {
        "slug":        "naruto-shippuden",
        "total_eps":   500,
        "mal_id":      1735,     # https://myanimelist.net/anime/1735
        "tmdb_id":     31910,    # https://www.themoviedb.org/tv/31910
        "output_file": "naruto_shippuden.json",
    },
]

# ── General settings ───────────────────────────────────────────────────────────
BASE_URL        = "https://www.animegg.org"
OUTPUT_DIR      = Path("output")          # folder where JSON files are saved
REQUEST_TIMEOUT = 15                      # seconds per HTTP request
RETRY_ATTEMPTS  = 3                       # retries on transient errors
RETRY_DELAY     = 5                       # seconds between retries
POLITE_DELAY    = 1.0                     # seconds between requests (be polite)
SEPARATOR       = "=" * 70


# ── URL builder ────────────────────────────────────────────────────────────────

def build_episode_url(slug: str, episode: int) -> str:
    """Return the full URL for a given slug and episode number.

    Pattern: https://www.animegg.org/{slug}-episode-{episode}
    e.g.     https://www.animegg.org/naruto-episode-1
             https://www.animegg.org/naruto-shippuden-episode-500
    """
    return f"{BASE_URL}/{slug}-episode-{episode}"


# ── HTTP helper ────────────────────────────────────────────────────────────────

def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.text
        except requests.exceptions.RequestException as exc:
            if attempt == RETRY_ATTEMPTS:
                raise
            print(f"    Attempt {attempt} failed ({exc}). Retrying in {RETRY_DELAY}s…")
            time.sleep(RETRY_DELAY)


# ── Extractors ─────────────────────────────────────────────────────────────────

def extract_page_title(soup: BeautifulSoup) -> str | None:
    tag = soup.find("title")
    return tag.get_text(strip=True) if tag else None


def extract_episode_number(url: str, title: str | None) -> int | None:
    m = re.search(r"episode-(\d+)", url, re.IGNORECASE)
    if m:
        return int(m.group(1))
    if title:
        m = re.search(r"episode\s+(\d+)", title, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


def extract_iframes(soup: BeautifulSoup, base_url: str) -> list[str]:
    srcs = []
    for tag in soup.find_all("iframe"):
        src = tag.get("src")
        if src:
            srcs.append(urljoin(base_url, src))
    return srcs


# ── Per-series scraper ─────────────────────────────────────────────────────────

def scrape_series(series: dict) -> list[dict]:
    slug       = series["slug"]
    total_eps  = series["total_eps"]
    mal_id     = series["mal_id"]
    tmdb_id    = series["tmdb_id"]

    print(f"\n{'#' * 70}")
    print(f"  Scraping: {slug}  ({total_eps} episodes)")
    print(f"{'#' * 70}\n")

    records = []

    for ep in range(1, total_eps + 1):
        url = build_episode_url(slug, ep)
        print(f"[{ep:>4}/{total_eps}] {url}")

        try:
            html = fetch_html(url)
        except requests.exceptions.RequestException as exc:
            print(f"         ERROR: {exc}")
            records.append({
                "mal_id":     mal_id,
                "tmdb_id":    tmdb_id,
                "title":      None,
                "episode_no": ep,
                "sub":        None,
                "dub":        None,
                "error":      str(exc),
            })
            time.sleep(POLITE_DELAY)
            continue

        soup        = BeautifulSoup(html, "html.parser")
        page_title  = extract_page_title(soup)
        iframe_srcs = extract_iframes(soup, url)

        sub = iframe_srcs[0] if len(iframe_srcs) > 0 else None
        dub = iframe_srcs[1] if len(iframe_srcs) > 1 else None

        print(f"         Title : {page_title}")
        print(f"         Sub   : {sub}")
        print(f"         Dub   : {dub}")

        records.append({
            "mal_id":     mal_id,
            "tmdb_id":    tmdb_id,
            "title":      page_title,
            "episode_no": ep,
            "sub":        sub,
            "dub":        dub,
        })

        time.sleep(POLITE_DELAY)

    return records


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for series in SERIES:
        records   = scrape_series(series)
        out_path  = OUTPUT_DIR / series["output_file"]

        out_path.write_text(
            json.dumps(records, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\n✓ Saved {len(records)} records → {out_path}\n")

    print(SEPARATOR)
    print("All series done.")


if __name__ == "__main__":
    main()

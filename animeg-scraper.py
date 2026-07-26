#!/usr/bin/env python3
"""
AnimeGG Scraper — GitHub Actions Edition
- Reads SCRAPER_START / SCRAPER_END from env (set by workflow)
- Auto-resumes via data/state.json when env vars not set
- Auto-splits JSON at 3 MB → animeg_part_1.json, animeg_part_2.json …
- Saves data/index.json listing all parts
"""

import requests
from bs4 import BeautifulSoup
import json
import time
import os
import sys

# ─── Constants ────────────────────────────────────────────
BASE_URL      = "https://www.animegg.org"
DATA_DIR      = "data"
STATE_FILE    = os.path.join(DATA_DIR, "state.json")
INDEX_FILE    = os.path.join(DATA_DIR, "index.json")
MAX_PAGE      = 6500
MAX_FILE_SIZE = 3 * 1024 * 1024   # 3 MB
PAGES_PER_RUN = 1000              # default when no end given
DELAY         = 1.2               # seconds between requests
# ──────────────────────────────────────────────────────────


# ── State ─────────────────────────────────────────────────

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"next_page": 0, "part": 1, "total_urls": 0}

def save_state(state):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ── Part files ────────────────────────────────────────────

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
    """Fast size estimate without full JSON dump every time."""
    return sum(
        len(e["episode_title"].encode()) + len(e["episode_href"].encode()) + 40
        for e in data
    )


# ── Index ─────────────────────────────────────────────────

def update_index(parts):
    entries = []
    for n in sorted(parts):
        p = part_path(n)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        entries.append({
            "part":        n,
            "file":        f"animeg_part_{n}.json",
            "entries":     len(data),
            "size_bytes":  os.path.getsize(p),
            "serial_range": [
                data[0]["serial_no"]  if data else None,
                data[-1]["serial_no"] if data else None,
            ],
        })
    with open(INDEX_FILE, "w") as f:
        json.dump(entries, f, indent=2)
    total = sum(e["entries"] for e in entries)
    print(f"[index] {len(entries)} parts | {total} total entries")


# ── HTTP ──────────────────────────────────────────────────

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
        s.get(BASE_URL, timeout=15)   # seed cookies
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


# ── Parse ─────────────────────────────────────────────────

def parse_episodes(html, serial_offset):
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
            strong = a.find("strong")
            title  = strong.get_text(strip=True) if strong else a.get_text(strip=True)
            href   = BASE_URL + a["href"]
            results.append({
                "serial_no":     serial_offset + len(results) + 1,
                "episode_title": title,
                "episode_href":  href,
            })
    return results


# ── Main ──────────────────────────────────────────────────

def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    # ── Resolve page range ────────────────────────────────
    # SCRAPER_START / SCRAPER_END are injected by the workflow.
    # When running locally without them, fall back to state.json.
    state = load_state()

    env_start = os.environ.get("SCRAPER_START", "").strip()
    env_end   = os.environ.get("SCRAPER_END",   "").strip()

    if env_start != "":
        start_page = int(env_start)
    else:
        start_page = state.get("next_page", 0)

    if env_end != "":
        end_page = int(env_end)
    else:
        end_page = start_page + PAGES_PER_RUN - 1

    # Safety clamp
    start_page = max(0, min(start_page, MAX_PAGE))
    end_page   = max(start_page, min(end_page, MAX_PAGE))

    if start_page > MAX_PAGE:
        print(f"All pages up to {MAX_PAGE} already scraped.")
        sys.exit(0)

    # ── When manual range is given, serial_no starts fresh
    #    for that part; when auto-resuming, continue from state.
    if env_start != "":
        # Manual run: serial continues from global total so far
        serial_offset = state.get("total_urls", 0)
    else:
        serial_offset = state.get("total_urls", 0)

    current_part = state.get("part", 1)

    print(f"\n{'='*58}")
    print(f"  AnimeGG Scraper")
    print(f"{'='*58}")
    print(f"  Page range  : {start_page} → {end_page}  "
          f"({end_page - start_page + 1} pages)")
    print(f"  URL range   : ?start={start_page*10} → ?start={end_page*10}")
    print(f"  Serial off  : {serial_offset}")
    print(f"  Current part: animeg_part_{current_part}.json")
    print(f"{'='*58}\n")

    session      = make_session()
    buffer       = load_part(current_part)
    parts_used   = {current_part}
    urls_scraped = 0
    empty_streak = 0

    for page in range(start_page, end_page + 1):
        pct = ((page - start_page) / max(end_page - start_page, 1)) * 100
        print(f"[{pct:5.1f}%] page {page:>5} (?start={page*10:<6}) | ", end="", flush=True)

        html = fetch_page(session, page)

        if html is None:
            print("SKIPPED (fetch failed)")
            empty_streak += 1
        else:
            episodes = parse_episodes(html, serial_offset)

            if not episodes:
                print("0 episodes")
                empty_streak += 1
                if empty_streak >= 5:
                    print("\n[stop] 5 consecutive empty/failed pages — end of content.")
                    break
            else:
                empty_streak = 0
                print(f"{len(episodes):>3} episodes | "
                      f"serial {serial_offset+1}–{serial_offset+len(episodes)}")

                for ep in episodes:
                    buffer.append(ep)
                    serial_offset += 1
                    urls_scraped  += 1

                    # Check split every 100 entries (fast estimate)
                    if len(buffer) % 100 == 0:
                        if estimated_size(buffer) >= MAX_FILE_SIZE:
                            save_part(current_part, buffer)
                            size_kb = part_size(current_part) / 1024
                            print(f"\n  ↳ [SPLIT] Part {current_part} "
                                  f"({size_kb:.0f} KB ≥ 3 MB) "
                                  f"→ starting part {current_part + 1}")
                            current_part += 1
                            parts_used.add(current_part)
                            buffer = []

        # Save after every page (crash-safe)
        save_part(current_part, buffer)
        parts_used.add(current_part)

        # Update state after every page
        save_state({
            "next_page":  page + 1,
            "part":       current_part,
            "total_urls": serial_offset,
        })

        if page < end_page:
            time.sleep(DELAY)

    # Final save & index
    save_part(current_part, buffer)
    update_index(parts_used)

    print(f"\n{'='*58}")
    print(f"  Done!")
    print(f"  Pages scraped : {start_page} → {end_page}")
    print(f"  URLs this run : {urls_scraped}")
    print(f"  Total URLs    : {serial_offset}")
    print(f"  Parts written : {sorted(parts_used)}")
    print(f"  Next run page : {end_page + 1}")
    print(f"{'='*58}\n")


if __name__ == "__main__":
    main()

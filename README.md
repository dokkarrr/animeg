# AnimeGG Scraper

Scrapes episode embed URLs (sub + dub iframes) from [animegg.org](https://www.animegg.org) and saves them as structured JSON files.

## Output format

Each series produces a JSON file like `output/naruto.json`:

```json
[
  {
    "mal_id": 20,
    "tmdb_id": 46260,
    "title": "Watch Naruto Episode 1 | Subbed Dubbed Animegg",
    "episode_no": 1,
    "sub": "https://www.animegg.org/embed/12345",
    "dub": "https://www.animegg.org/embed/67890"
  }
]
```

## Local usage

```bash
pip install -r requirements.txt
python scraper.py
```

Output JSON files are saved to the `output/` folder.

## Adding a new anime

Edit the `SERIES` list in `scraper.py`:

```python
SERIES = [
    {
        "slug":        "one-piece",   # slug used in animegg.org URLs
        "total_eps":   1000,
        "mal_id":      21,
        "tmdb_id":     37854,
        "output_file": "one_piece.json",
    },
    ...
]
```

The scraper will auto-generate URLs like:
```
https://www.animegg.org/one-piece-episode-1
https://www.animegg.org/one-piece-episode-2
...
https://www.animegg.org/one-piece-episode-1000
```

## GitHub Actions

The workflow (`.github/workflows/scrape.yml`) runs automatically:

| Trigger | When |
|---|---|
| **Schedule** | Every Sunday at 00:00 UTC |
| **Manual** | Via the Actions tab → "Run workflow" |
| **Push** | On every push to `main` |

After scraping, the workflow commits the updated JSON files back to the repo automatically.

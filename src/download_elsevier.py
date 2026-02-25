#!/usr/bin/env python
from __future__ import annotations

"""
Fetch full-text XML for Cell journal articles via the Elsevier Article Retrieval API.

    Reads metadata from metadata/**/*.json, filters to cell.com URLs (Cell, Cell Immunity,
    etc.), skips articles already downloaded, and writes to data/elsevier/<year>/<journal>/<pii>.xml.
    Uses ENTITLED view to check access; upgrades to FULL view for Open Access articles.
    """

import argparse
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from tqdm import tqdm

from common import load_articles, path_safe_journal, setup_proxy, year_from_date
from config import get_journal_info

BASE_URL: str = "https://api.elsevier.com/content/article/pii"
DEFAULT_DATA_GLOB: str = "metadata/**/*.json"
CHROME_CELL_GLOB: str = "chrome/cell/**/*.json"
CHROME_IMMUNITY_GLOB: str = "chrome/immunity/**/*.json"
MAX_RETRIES: int = 3
RETRY_BACKOFF_SEC: int = 5
PARALLEL_WORKERS: int = 12

_CONTENT_TAGS: tuple[str, ...] = ("<originalText>", "<body>", "<ce:sections>", "<abstract>")


def is_cell_url(url: str) -> bool:
    """Check if the URL belongs to Cell or Elsevier.
    
    Args:
        url: The article URL.
        
    Returns:
        True if it's a Cell/Elsevier URL.
    """
    return bool(url) and ("cell.com" in url or "elsevier.com" in url)


def is_elsevier_journal(journal: str) -> bool:
    """Check if the journal is an Elsevier journal.
    
    Args:
        journal: The journal name.
        
    Returns:
        True if it's an Elsevier journal.
    """
    info = get_journal_info(journal)
    return info is not None and info["abbr"] in ("cell", "immunity")


def pii_from_url(url: str) -> str | None:
    """Extract the PII from the last path segment of an Elsevier/Cell URL.
    
    Args:
        url: The article URL.
        
    Returns:
        The PII string or None.
    """
    url = (url or "").strip().rstrip("/")
    if not url:
        return None
    pii = url.split("/")[-1].split("?")[0]
    # Handle cases like 'S0092-8674(26)00056-5' (Cell)
    return pii or None


def pii_to_compact(pii: str) -> str:
    """Convert display PII (e.g. S0092-8674(25)01179-1) to compact form for the API.
    
    Args:
        pii: The display PII.
        
    Returns:
        The compact PII string.
    """
    return pii.replace("-", "").replace("(", "").replace(")", "")


def article_output_path(article: dict[str, Any]) -> Path | None:
    """Compute output path: data/elsevier/<year>/<journal>/<article_id>.xml
    
    Args:
        article: The article metadata dictionary.
        
    Returns:
        The Path object for the output file or None.
    """
    pii = pii_from_url(article.get("url", ""))
    if not pii:
        return None
    year = year_from_date(article.get("date") or "")
    journal = path_safe_journal(article.get("journal") or "")
    return Path("data/elsevier") / year / journal / f"{pii.replace('/', '_')}.xml"


def _has_full_content(text: str) -> bool:
    """Check if the XML response has full content.
    
    Args:
        text: The XML response text.
        
    Returns:
        True if full content is present.
    """
    return any(tag in text for tag in _CONTENT_TAGS)


def fetch_article(api_key: str, pii: str) -> tuple[str | None, str | None]:
    """
    Fetch article XML for the given display PII.

    Returns (xml_text, error_message). Tries ENTITLED first; if Open Access,
    retries with FULL view to obtain the complete article body.
    Retries up to MAX_RETRIES times on 429 and network errors.
    
    Args:
        api_key: The Elsevier API key.
        pii: The display PII of the article.
        
    Returns:
        A tuple of (XML content or None, error message or None).
    """
    headers = {"X-ELS-APIKey": api_key, "Accept": "text/xml"}
    url = f"{BASE_URL}/{quote(pii_to_compact(pii), safe='')}"

    last_error: str | None = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, headers=headers, params={"view": "ENTITLED"}, timeout=60)

            if resp.status_code == 429:
                # Check for rate limit reset time
                reset_time = resp.headers.get("X-RateLimit-Reset")
                if reset_time:
                    try:
                        wait_sec = max(0, int(reset_time) - int(time.time())) + 1
                        if wait_sec > 3600: # If wait is more than an hour, maybe it's the weekly quota
                            return None, f"Weekly quota exceeded. Resets in {wait_sec/3600:.1f} hours"
                        print(f"\n[429] Rate limit hit. Waiting {wait_sec}s for reset (PII: {pii})")
                        time.sleep(wait_sec)
                        continue
                    except (ValueError, TypeError):
                        pass
                
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_BACKOFF_SEC * (attempt + 1))
                continue

            if not resp.ok:
                last_error = f"HTTP {resp.status_code}"
                break

            if "<document-entitlement>" not in resp.text:
                if _has_full_content(resp.text):
                    return resp.text, None
                last_error = "ENTITLED returned no recognizable content"
                break

            if "<status>OPEN_ACCESS</status>" not in resp.text:
                return None, "not entitled (closed access)"

            resp_full = requests.get(url, headers=headers, params={"view": "FULL"}, timeout=60)
            if resp_full.status_code == 429:
                reset_time = resp_full.headers.get("X-RateLimit-Reset")
                if reset_time:
                    try:
                        wait_sec = max(0, int(reset_time) - int(time.time())) + 1
                        if wait_sec > 3600:
                            return None, f"Weekly quota exceeded. Resets in {wait_sec/3600:.1f} hours"
                        print(f"\n[429] Rate limit hit (FULL). Waiting {wait_sec}s for reset (PII: {pii})")
                        time.sleep(wait_sec)
                        continue
                    except (ValueError, TypeError):
                        pass
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_BACKOFF_SEC * (attempt + 1))
                continue

            if resp_full.ok and "<document-entitlement>" not in resp_full.text and _has_full_content(resp_full.text):
                return resp_full.text, None
            
            return None, "OA but FULL view returned no usable content"

        except requests.RequestException as e:
            last_error = str(e)
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_SEC * (attempt + 1))

    return None, last_error or "unknown error"


def main() -> None:
    """Main entry point for the Elsevier downloader."""
    parser = argparse.ArgumentParser(description="Fetch Elsevier articles from metadata.")
    parser.add_argument("--debug", action="store_true", help="Work on 10 random articles only.")
    parser.add_argument(
        "--data-glob", type=str, default=None,
        help=f"Glob for metadata JSON files (default: {DEFAULT_DATA_GLOB}, {CHROME_CELL_GLOB}, {CHROME_IMMUNITY_GLOB}).",
    )
    args = parser.parse_args()

    load_dotenv()
    api_key = os.environ.get("ELSEVIER_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("ELSEVIER_API_KEY not set in .env")

    data_glob = args.data_glob
    if data_glob is None:
        data_glob = (DEFAULT_DATA_GLOB, CHROME_CELL_GLOB, CHROME_IMMUNITY_GLOB)

    articles = load_articles(data_glob)

    tasks: list[tuple[str, Path]] = []
    seen_piis: set[str] = set()
    duplicated = 0
    not_cell = 0
    already_exists = 0
    no_pii = 0

    for article in articles:
        url = article.get("url", "")
        journal = article.get("journal", "")
        if not is_cell_url(url) and not is_elsevier_journal(journal):
            not_cell += 1
            continue
        pii = pii_from_url(url)
        if not pii:
            no_pii += 1
            continue
        
        if pii in seen_piis:
            duplicated += 1
            continue
        seen_piis.add(pii)

        out_path = article_output_path(article)
        if out_path is None:
            continue
        if out_path.exists():
            content = out_path.read_text(encoding="utf-8")
            if "<document-entitlement>" not in content:
                already_exists += 1
                continue
        
        tasks.append((pii, out_path))

    if args.debug:
        print("Debug mode: selecting 10 random articles.")
        tasks = random.sample(tasks, min(len(tasks), 10))
    else:
        random.shuffle(tasks)

    print(f"Total articles found: {len(articles)}")
    print(f"Articles to process: {len(tasks)}")
    print(f"  Already exists: {already_exists}")
    print(f"  Not Cell:       {not_cell}")

    saved = skipped = errors = 0
    start_time = time.perf_counter()

    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as executor:
        future_to_task = {
            executor.submit(fetch_article, api_key, pii): (pii, out_path)
            for pii, out_path in tasks
        }
        with tqdm(total=len(tasks), desc="Fetching articles", unit="art") as pbar:
            for future in as_completed(future_to_task):
                pii, out_path = future_to_task[future]
                try:
                    xml_text, error = future.result()
                    if xml_text is not None:
                        out_path.parent.mkdir(parents=True, exist_ok=True)
                        out_path.write_text(xml_text, encoding="utf-8")
                        saved += 1
                    elif error and "not entitled" not in error:
                        pbar.write(f"Error {pii}: {error}")
                        errors += 1
                    else:
                        skipped += 1
                except Exception as e:
                    pbar.write(f"Exception {pii}: {e}")
                    errors += 1
                pbar.update(1)

    elapsed = time.perf_counter() - start_time
    print(f"\n--- Stats ---")
    print(f"  Saved:        {saved}")
    print(f"  Skipped:      {skipped} (closed access)")
    print(f"  Errors:       {errors}")
    print(f"  Total tasks:  {len(tasks)}")
    print(f"  Time:         {elapsed:.1f}s")


if __name__ == "__main__":
    setup_proxy()
    main()

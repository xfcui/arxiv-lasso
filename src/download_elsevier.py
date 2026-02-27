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

from common import load_articles, log, path_safe_journal, setup_proxy, year_from_date
from config import get_journal_info

BASE_URL: str = "https://api.elsevier.com/content/article/pii"
DEFAULT_DATA_GLOB: str = "metadata/**/*.json"
CHROME_CELL_GLOB: str = "chrome/cell/**/*.json"
CHROME_IMMUNITY_GLOB: str = "chrome/immunity/**/*.json"
MAX_RETRIES: int = 3
RETRY_BACKOFF_SEC: int = 5
PARALLEL_WORKERS: int = 4

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


def article_output_paths(article: dict[str, Any]) -> tuple[Path | None, Path | None]:
    """Compute output paths: 
    - Metadata: data/elsevier/<year>/<journal>/<article_id>_meta.xml
    - Full-text: data/elsevier/<year>/<journal>/<article_id>.xml
    
    Args:
        article: The article metadata dictionary.
        
    Returns:
        A tuple of (metadata Path, fulltext Path) or (None, None).
    """
    pii = pii_from_url(article.get("url", ""))
    if not pii:
        return None, None
    year = year_from_date(article.get("date") or "")
    journal = path_safe_journal(article.get("journal") or "")
    base_path = Path("data/elsevier") / year / journal
    safe_pii = pii.replace("/", "_")
    return base_path / f"{safe_pii}_meta.xml", base_path / f"{safe_pii}.xml"


def _has_full_content(text: str) -> bool:
    """Check if the XML response has full content.
    
    Args:
        text: The XML response text.
        
    Returns:
        True if full content is present.
    """
    return any(tag in text for tag in _CONTENT_TAGS)


def fetch_article(api_key: str, pii: str, meta_path: Path, xml_path: Path, force: bool = False) -> tuple[bool, str | None]:
    """
    Fetch article metadata and full-text XML for the given display PII.

    Returns (success, error_message). 
    1. Fetches metadata (ENTITLED view) and saves to meta_path if it doesn't exist.
    2. If Open Access (or force=True), retries with FULL view to obtain the complete article body and saves to xml_path if it doesn't exist.
    
    Retries up to MAX_RETRIES times on 429 and network errors.
    
    Args:
        api_key: The Elsevier API key.
        pii: The display PII of the article.
        meta_path: The path to save the metadata XML.
        xml_path: The path to save the full-text XML.
        force: If True, attempt full-text download even for non-OA articles.
        
    Returns:
        A tuple of (success boolean, error message or None).
    """
    headers = {"X-ELS-APIKey": api_key, "Accept": "text/xml"}
    url = f"{BASE_URL}/{quote(pii_to_compact(pii), safe='')}"

    last_error: str | None = None
    is_oa = False
    
    # Step 1: Handle metadata
    if meta_path.exists():
        try:
            content = meta_path.read_text(encoding="utf-8")
            is_oa = "<status>OPEN_ACCESS</status>" in content
        except Exception as e:
            log(f"Error reading existing metadata for {pii}: {e}", level="WARNING")
    else:
        for attempt in range(MAX_RETRIES):
            try:
                resp = requests.get(url, headers=headers, params={"view": "ENTITLED"}, timeout=60)

                if resp.status_code == 429:
                    # Check for rate limit reset time
                    reset_time = resp.headers.get("X-RateLimit-Reset")
                    if reset_time:
                        try:
                            wait_sec = max(0, int(reset_time) - int(time.time())) + 1
                            if wait_sec > 300: # If wait is more than an hour, maybe it's the weekly quota
                                return False, f"Weekly quota exceeded. Resets in {wait_sec/3600:.1f} hours"
                            log(f"Rate limit hit. Waiting {wait_sec}s for reset (PII: {pii})", level="WARNING")
                            time.sleep(wait_sec)
                            continue
                        except (ValueError, TypeError):
                            pass
                    
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(RETRY_BACKOFF_SEC * (attempt + 1))
                    continue

                if not resp.ok:
                    last_error = f"HTTP {resp.status_code} (ENTITLED)"
                    break

                if "<document-entitlement>" not in resp.text:
                    last_error = "ENTITLED returned no recognizable content"
                    break

                # Save metadata
                meta_path.parent.mkdir(parents=True, exist_ok=True)
                meta_path.write_text(resp.text, encoding="utf-8")
                is_oa = "<status>OPEN_ACCESS</status>" in resp.text
                last_error = None # Clear any previous errors
                break

            except requests.RequestException as e:
                last_error = str(e)
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_BACKOFF_SEC * (attempt + 1))
        
        if last_error:
            return False, last_error

    # Step 2: Handle full-text
    if xml_path.exists():
        return True, None

    if not is_oa and not force:
        return True, "not entitled (closed access) - metadata exists"

    for attempt in range(MAX_RETRIES):
        try:
            with requests.get(url, headers=headers, params={"view": "FULL"}, timeout=60, stream=True) as resp_full:
                if resp_full.status_code == 429:
                    reset_time = resp_full.headers.get("X-RateLimit-Reset")
                    if reset_time:
                        try:
                            wait_sec = max(0, int(reset_time) - int(time.time())) + 1
                            if wait_sec > 3600:
                                return False, f"Weekly quota exceeded. Resets in {wait_sec/3600:.1f} hours"
                            log(f"Rate limit hit (FULL). Waiting {wait_sec}s for reset (PII: {pii})", level="WARNING")
                            time.sleep(wait_sec)
                            continue
                        except (ValueError, TypeError):
                            pass
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(RETRY_BACKOFF_SEC * (attempt + 1))
                    continue

                if resp_full.ok:
                    xml_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(xml_path, "wb") as f:
                        for chunk in resp_full.iter_content(chunk_size=8192):
                            f.write(chunk)
                    return True, None
                
                last_error = f"HTTP {resp_full.status_code} (FULL)"
                break
        
        except requests.RequestException as e:
            last_error = str(e)
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_SEC * (attempt + 1))

    return True, f"OA but FULL view failed: {last_error} - metadata saved/exists"


def main() -> None:
    """Main entry point for the Elsevier downloader."""
    parser = argparse.ArgumentParser(description="Fetch Elsevier articles from metadata.")
    parser.add_argument("--debug", action="store_true", help="Work on 10 random articles only.")
    parser.add_argument("--force", action="store_true", help="Attempt full-text download even for non-OA articles.")
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

    tasks: list[tuple[str, Path, Path]] = []
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

        meta_path, xml_path = article_output_paths(article)
        if meta_path is None or xml_path is None:
            continue
        
        # Check if we already have what we need
        if meta_path.exists() and xml_path.exists():
            already_exists += 1
            continue
        
        tasks.append((pii, meta_path, xml_path))

    if args.debug:
        log("Debug mode: selecting 10 random articles.")
        tasks = random.sample(tasks, min(len(tasks), 10))
    else:
        random.shuffle(tasks)

    log(f"Total articles found: {len(articles)}")
    log(f"Articles to process: {len(tasks)}")
    log(f"  Already exists: {already_exists}")
    log(f"  Not Cell:       {not_cell}")

    saved = skipped = errors = 0
    start_time = time.perf_counter()

    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as executor:
        future_to_task = {
            executor.submit(fetch_article, api_key, pii, meta_path, xml_path, force=args.force): (pii, meta_path, xml_path)
            for pii, meta_path, xml_path in tasks
        }
        with tqdm(total=len(tasks), desc="Fetching articles", unit="art") as pbar:
            for future in as_completed(future_to_task):
                pii, meta_path, xml_path = future_to_task[future]
                try:
                    success, error = future.result()
                    if success:
                        saved += 1
                        if error and "not entitled" in error:
                            skipped += 1
                    elif error:
                        pbar.write(f"Error {pii}: {error}")
                        errors += 1
                except Exception as e:
                    pbar.write(f"Exception {pii}: {e}")
                    errors += 1
                pbar.update(1)

    elapsed = time.perf_counter() - start_time
    log("--- Elsevier Stats ---")
    log(f"  Processed:    {saved}")
    log(f"  Skipped:      {skipped} (closed access metadata saved)")
    log(f"  Errors:       {errors}")
    log(f"  Total tasks:  {len(tasks)}")
    log(f"  Time:         {elapsed:.1f}s")


if __name__ == "__main__":
    setup_proxy()
    main()

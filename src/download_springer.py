#!/usr/bin/env python
from __future__ import annotations

"""
Download full-text JATS XML for Nature journal articles from the Springer Nature Open Access API.

Reads metadata from metadata/**/*.json, filters to nature.com URLs (Nature, Nature Methods,
Nature Biotechnology, Nature Machine Intelligence, etc.), skips articles whose output file
already exists, and writes to data/springer/<year>/<journal>/<article_id>.xml.
Batches up to 10 articles per request to reduce API usage (Requests/Day limit).

Note: The Open Access JATS API often returns accepted-manuscript style XML with
<front> and <back> but no <body>. Articles without <body> are not saved; their IDs
are appended to nobody.log.
"""

import argparse
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from tqdm import tqdm

from common import load_articles, path_safe_journal, setup_proxy, year_from_date
from config import get_journal_info

DEFAULT_DATA_GLOB: list[str] = ["metadata/**/*.json", "chrome/nature/**/*.json", "chrome/ni/**/*.json", "chrome/search/**/*.json"]
DEFAULT_OUTPUT_DIR: str = "data/springer"
JATS_BASE_URL: str = "https://api.springernature.com/openaccess/jats"
BATCH_SIZE: int = 10
PARALLEL_WORKERS: int = 5
MAX_RETRIES: int = 3
RETRY_BACKOFF_SEC: int = 5


def load_env_api_key() -> str:
    """Load NATURE_API_KEY from .env. Raise if missing or empty.
    
    Returns:
        The API key string.
    """
    load_dotenv()
    key = os.environ.get("NATURE_API_KEY", "").strip()
    if not key:
        raise SystemExit("Error: NATURE_API_KEY not set or empty in .env")
    return key


def is_springer_url(url: str) -> bool:
    """Check if the URL belongs to Springer Nature.
    
    Args:
        url: The article URL.
        
    Returns:
        True if it's a Springer URL.
    """
    return bool(url) and ("nature.com" in url or "springer.com" in url)


def is_springer_journal(journal: str) -> bool:
    """Check if the journal is a Springer Nature journal.
    
    Args:
        journal: The journal name.
        
    Returns:
        True if it's a Springer journal.
    """
    if not journal:
        return False
    
    if journal.lower().startswith("nature"):
        return True
    
    # Check if it's in our known map
    info = get_journal_info(journal)
    if info is not None and info["abbr"] in ("nature", "ni"):
        return True
        
    # Fallback: any journal with "Nature" in the name is likely a Springer Nature journal
    return "nature" in journal.lower()


def article_id_from_url(url: str) -> str | None:
    """Extract the article ID from a nature.com or springer.com URL.
    
    Args:
        url: The article URL.
        
    Returns:
        The article ID or None.
    """
    url = (url or "").strip().rstrip("/")
    if "/articles/" in url:
        aid = url.split("/articles/")[-1].split("?")[0]
        return aid or None
    return None


def output_path(article: dict[str, Any], output_dir: str) -> Path | None:
    """Compute output path: <output_dir>/<year>/<journal>/<article_id>.xml
    
    Args:
        article: The article metadata dictionary.
        output_dir: The base output directory.
        
    Returns:
        The Path object for the output file or None.
    """
    aid = article_id_from_url(article.get("url", ""))
    if not aid:
        return None
    date_val = article.get("date") or ""
    year = year_from_date(date_val)
    # #region agent log
    from datetime import datetime
    with open('/home/gateway/ncbi/.cursor/debug-cd2cd4.log', 'a') as f:
        import json as j; f.write(j.dumps({"sessionId":"cd2cd4","hypothesisId":"D","location":"download_springer.py:111","message":"Output path calculation","data":{"url":article.get("url"),"date":date_val,"year":year},"timestamp":int(datetime.now().timestamp()*1000)}) + "\n")
    # #endregion
    journal = path_safe_journal(article.get("journal") or "")
    return Path(output_dir) / year / journal / f"{aid}.xml"


def fetch_jats_batch(api_key: str, dois: list[str]) -> requests.Response:
    """GET JATS XML for multiple DOIs in one request (q=doi:A OR doi:B OR ...).
    
    Args:
        api_key: The Springer Nature API key.
        dois: A list of DOIs to fetch.
        
    Returns:
        The requests.Response object.
    """
    q = " OR ".join(f"doi:{d}" for d in dois)
    if len(dois) > 1:
        q = f"({q})"
    return requests.get(
        JATS_BASE_URL,
        params={"api_key": api_key, "q": q, "p": len(dois)},
        timeout=60,
    )


def _strip_ns(tag: str) -> str:
    """Strip namespace from XML tag.
    
    Args:
        tag: The XML tag string.
        
    Returns:
        The tag without namespace.
    """
    return tag.split("}")[-1] if "}" in tag else tag


def extract_article_id_from_jats(article_elem: ET.Element) -> str | None:
    """Get publisher-id from JATS <article-id pub-id-type='publisher-id'>.
    
    Args:
        article_elem: The XML element for the article.
        
    Returns:
        The publisher ID or None.
    """
    for elem in article_elem.iter():
        if _strip_ns(elem.tag) == "article-id" and elem.get("pub-id-type") == "publisher-id":
            return (elem.text or "").strip() or None
    return None


def article_has_body(article_elem: ET.Element) -> bool:
    """Return True if the JATS <article> has a <body> element.
    
    Args:
        article_elem: The XML element for the article.
        
    Returns:
        True if the article has a body.
    """
    return any(_strip_ns(child.tag) == "body" for child in article_elem)


def parse_batch_jats_response(text: str) -> tuple[dict[str, str], set[str]]:
    """
    Parse a JATS batch response; return (article_id → XML string, no-body IDs).
    Only saves the inner <article> element (no wrapper).
    
    Args:
        text: The XML response text.
        
    Returns:
        A tuple of (mapping of ID to XML content, set of IDs without body).
    """
    result: dict[str, str] = {}
    no_body_ids: set[str] = set()

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return result, no_body_ids

    records = next(
        (elem for elem in root.iter() if _strip_ns(elem.tag) == "records"),
        None,
    )
    if records is None:
        return result, no_body_ids

    for child in records:
        if _strip_ns(child.tag) != "article":
            continue
        aid = extract_article_id_from_jats(child)
        if not aid:
            continue
        if not article_has_body(child):
            no_body_ids.add(aid)
            continue
        result[aid] = '<?xml version="1.0"?>\n' + ET.tostring(child, encoding="unicode")

    return result, no_body_ids


def process_one_batch(
    api_key: str,
    batch: list[tuple[dict[str, Any], Path, str]],
) -> tuple[list[dict[str, Any]], set[str], bool]:
    """
    Fetch JATS for one batch of (article, out_path, doi), write XML files.
    Return (failures, article_ids_with_no_body, stop_requested).
    
    Args:
        api_key: The API key.
        batch: A list of (article metadata, output path, DOI) tuples.
        
    Returns:
        A tuple of (list of failure info, set of IDs without body, bool stop_requested).
    """
    dois = [doi for _, _, doi in batch]
    id_to_path = {article_id_from_url(a["url"]): p for a, p, _ in batch}
    id_to_doi = {article_id_from_url(a["url"]): d for a, _, d in batch}
    id_to_url = {article_id_from_url(a["url"]): a.get("url", "") for a, _, _ in batch}

    last_error: str | None = None
    for attempt in range(MAX_RETRIES):
        try:
            r = fetch_jats_batch(api_key, dois)
            if r.status_code != 200:
                last_error = f"HTTP {r.status_code}"
                if r.status_code == 429:
                    print("\nError 429: Too Many Requests. Stopping download.")
                    return [], set(), True
                if r.status_code >= 500:
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(RETRY_BACKOFF_SEC * (attempt + 1))
                    continue
                break
            if not (r.text or "").strip():
                last_error = "empty response body"
                break

            by_id, no_body_ids = parse_batch_jats_response(r.text)
            for aid, xml_content in by_id.items():
                p = id_to_path.get(aid)
                if p is not None:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(xml_content, encoding="utf-8")

            failures = [
                {"url": id_to_url.get(aid, ""), "doi": id_to_doi.get(aid), "reason": "not in batch response"}
                for aid in id_to_path
                if aid not in by_id and aid not in no_body_ids
            ]
            return failures, no_body_ids, False

        except requests.RequestException as e:
            last_error = str(e)
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_SEC * (attempt + 1))

    failures = [{"doi": doi, "reason": last_error or "unknown"} for _, _, doi in batch]
    return failures, set(), False


def main() -> None:
    """Main entry point for the Springer downloader."""
    parser = argparse.ArgumentParser(
        description="Download full-text JATS XML for Nature open-access articles."
    )
    parser.add_argument("--limit", type=int, default=None, help="Process at most N articles.")
    parser.add_argument(
        "--output-dir", type=str, default=DEFAULT_OUTPUT_DIR,
        help=f"Output root directory (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--data-glob", type=str, nargs="*", default=None, metavar="GLOB",
        help=f"Glob(s) for metadata JSON files (default: {DEFAULT_DATA_GLOB}).",
    )
    parser.add_argument(
        "--batch-size", type=int, default=BATCH_SIZE,
        help=f"Articles per API request (default: {BATCH_SIZE}).",
    )
    args = parser.parse_args()

    data_glob = tuple(args.data_glob) if args.data_glob else DEFAULT_DATA_GLOB
    api_key = load_env_api_key()
    articles = load_articles(data_glob)
    if args.limit is not None:
        articles = articles[: args.limit]

    print(f"Total articles found: {len(articles)}")

    failures: list[dict] = []
    articles_without_body: set[str] = set()
    to_fetch: list[tuple[dict, Path, str]] = []
    already_exists = 0
    not_springer = 0
    seen_dois: set[str] = set()
    duplicated = 0

    # Track stats per journal
    journal_stats: dict[str, dict[str, int]] = {}

    for article in articles:
        url = article.get("url", "")
        journal = article.get("journal", "")
        
        # Initialize journal stats
        if journal not in journal_stats:
            journal_stats[journal] = {"found": 0, "processed": 0, "saved": 0, "no_body": 0, "failed": 0, "already_exists": 0, "not_springer": 0}
        
        # Deduplication check for stats
        doi = (article.get("doi") or "").strip()
        if not doi:
            # Fallback for chrome/nature and chrome/ni where DOI might be missing in metadata
            # but can often be derived from the article ID (e.g. s41586-025-09840-z -> 10.1038/s41586-025-09840-z)
            aid = article_id_from_url(url)
            if aid and aid.startswith("s"):
                doi = f"10.1038/{aid}"
        
        if doi and doi in seen_dois:
            duplicated += 1
            continue
        if doi:
            seen_dois.add(doi)
        
        journal_stats[journal]["found"] += 1

        if not is_springer_url(url) and not is_springer_journal(journal):
            not_springer += 1
            journal_stats[journal]["not_springer"] += 1
            continue
        
        if not doi:
            failures.append({"url": url, "reason": "missing doi in metadata and could not derive from id"})
            journal_stats[journal]["failed"] += 1
            continue

        out_path = output_path(article, args.output_dir)
        if out_path is None:
            failures.append({"url": url, "reason": "could not derive article id"})
            journal_stats[journal]["failed"] += 1
            continue
        if out_path.exists():
            already_exists += 1
            journal_stats[journal]["already_exists"] += 1
            continue
        
        to_fetch.append((article, out_path, doi))
        journal_stats[journal]["processed"] += 1

    print(f"Articles to process: {len(to_fetch)}")
    print(f"  Already exists: {already_exists}")
    print(f"  Not Springer:   {not_springer}")

    batch_size = max(1, args.batch_size)
    batches = [to_fetch[i: i + batch_size] for i in range(0, len(to_fetch), batch_size)]

    # Map DOI back to journal for stats tracking
    doi_to_journal = {doi: a.get("journal", "") for a, _, doi in to_fetch}

    start_time = time.perf_counter()
    stop_requested_global = False
    with tqdm(total=len(to_fetch), desc="Downloading", unit="art") as pbar:
        with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as executor:
            futures = {
                executor.submit(process_one_batch, api_key, batch): batch
                for batch in batches
            }
            try:
                for future in as_completed(futures):
                    batch = futures[future]
                    try:
                        batch_failures, batch_no_body, stop_requested = future.result()
                        failures.extend(batch_failures)
                        articles_without_body |= batch_no_body
                        
                        # Update journal stats
                        for f_entry in batch_failures:
                            j_name = doi_to_journal.get(f_entry.get("doi"), "")
                            if j_name: journal_stats[j_name]["failed"] += 1
                        
                        for nb_id in batch_no_body:
                            # Find journal for this no-body ID
                            for a, p, doi in to_fetch:
                                if article_id_from_url(a.get("url", "")) == nb_id:
                                    journal_stats[a.get("journal", "")]["no_body"] += 1
                                    break
                        
                        if stop_requested:
                            stop_requested_global = True
                            # Cancel pending futures and break loop
                            executor.shutdown(wait=False, cancel_futures=True)
                            break

                    except Exception as e:
                        for _, _, doi in batch:
                            failures.append({"doi": doi, "reason": str(e)})
                            j_name = doi_to_journal.get(doi, "")
                            if j_name: journal_stats[j_name]["failed"] += 1
                    pbar.update(len(batch))
            finally:
                # Ensure we shutdown the executor (if not already done)
                executor.shutdown(wait=False, cancel_futures=True)

    # If we stopped early, mark all remaining to_fetch items as failed
    if stop_requested_global:
        # Find which DOIs were actually completed (either saved, failed, or no_body)
        completed_dois = set()
        for f in failures:
            if "doi" in f: completed_dois.add(f["doi"])
        # This is tricky because we don't have a simple list of saved DOIs here,
        # but we can infer them from the files that were created.
        # However, a simpler way is to track what was actually processed in the loop.
        
        # Let's refine the logic: any article in to_fetch that isn't accounted for
        # in failures or articles_without_body and doesn't have an output file
        # should be considered failed due to the early stop.
        for article, out_path, doi in to_fetch:
            if not out_path.exists() and doi not in completed_dois:
                # Check if it was one of the no_body ones
                aid = article_id_from_url(article.get("url", ""))
                if aid not in articles_without_body:
                    failures.append({"doi": doi, "reason": "stopped due to 429 error"})
                    j_name = article.get("journal", "")
                    if j_name in journal_stats:
                        journal_stats[j_name]["failed"] += 1

    # Finalize saved stats per journal
    for j_name, stats in journal_stats.items():
        # If stop_requested was True, some articles in journal_stats["processed"] 
        # might not have been actually processed or failed yet.
        # But for the final table, we want Saved to only reflect what was actually saved.
        stats["saved"] = stats["processed"] - stats["failed"] - stats["no_body"]

    elapsed = time.perf_counter() - start_time
    saved = len(to_fetch) - len(failures) - len(articles_without_body)

    print(f"\n--- Stats ---")
    print(f"{'Journal':<40} {'Found':<8} {'ToProc':<8} {'Saved':<8} {'Failed':<8} {'Exist':<8} {'Rate':<8}")
    print("-" * 95)
    for j_name in sorted(journal_stats.keys()):
        s = journal_stats[j_name]
        # Only show journals that belong to Springer/Nature
        if not (is_springer_journal(j_name) or "nature.com" in j_name.lower() or "springer" in j_name.lower()):
            continue

        total = s["found"]
        # Rate is 1 - (Failed / Found)
        rate = (1 - (s["failed"] / total)) * 100 if total > 0 else 0
        print(f"{j_name[:39]:<40} {s['found']:<8} {s['processed']:<8} {s['saved']:<8} {s['failed']:<8} {s['already_exists']:<8} {rate:>6.1f}%")
    
    print("-" * 95)
    print(f"  Total Saved:  {saved}")
    print(f"  No body:      {len(articles_without_body)}")
    print(f"  Failures:     {len(failures)}")
    print(f"  Total tasks:  {len(to_fetch)}")
    print(f"  Time:         {elapsed:.1f}s")

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    if failures:
        with open("noresponse.log", "a", encoding="utf-8") as f:
            for entry in failures:
                f.write(json.dumps(entry) + "\n")
    if articles_without_body:
        no_body_path = Path("nobody.log")
        with open(no_body_path, "a", encoding="utf-8") as f:
            for aid in sorted(articles_without_body):
                f.write(f"{aid}\n")
        print(
            f"Note: {len(articles_without_body)} article(s) have no <body>; see {no_body_path}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    setup_proxy()
    main()

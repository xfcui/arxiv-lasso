#!/usr/bin/env python
from __future__ import annotations

"""
Download metadata and full-text JATS XML for Nature/Springer journal articles.

Round 1: Download metadata in JSON format from /meta/v2.
Round 2: Download full-text in XML format from /openaccess/jats if the article is Open Access.

Output structure:
- Metadata: data/springer/{yyyy}/{journal}/{article_id}_meta.json
- Full-text: data/springer/{yyyy}/{journal}/{article_id}.xml
"""

import argparse
import json
import logging
import os
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from dotenv import load_dotenv
from tqdm import tqdm

from common import load_articles, path_safe_journal, setup_proxy, year_from_date
from config import get_journal_info

# --- Constants ---
DEFAULT_DATA_GLOB = ["metadata/**/*.json", "chrome/n*/**/*.json", "chrome/search/**/*.json"]
DEFAULT_OUTPUT_DIR = "data/springer"
META_BASE_URL = "https://api.springernature.com/meta/v2/json"
JATS_BASE_URL = "https://api.springernature.com/openaccess/jats"
BATCH_SIZE = 10
PARALLEL_WORKERS = 5
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

class SpringerAPI:
    """Handles interactions with the Springer Nature API."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _fetch_batch(self, base_url: str, dois: List[str]) -> Optional[requests.Response]:
        """Generic batch fetch with retry logic."""
        q = " OR ".join(f"doi:{d}" for d in dois)
        if len(dois) > 1:
            q = f"({q})"
        
        params = {"api_key": self.api_key, "q": q, "p": len(dois)}
        
        for attempt in range(MAX_RETRIES):
            try:
                r = requests.get(base_url, params=params, timeout=60)
                if r.status_code == 200:
                    return r
                if r.status_code == 429:
                    logger.error("Error 429: Too Many Requests. Stopping.")
                    return r
                if r.status_code >= 500:
                    time.sleep(RETRY_BACKOFF_SEC * (attempt + 1))
                    continue
                logger.warning(f"HTTP {r.status_code} for batch {dois[:2]}...")
                return r
            except requests.RequestException as e:
                logger.error(f"Request error: {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_BACKOFF_SEC * (attempt + 1))
        return None

    def fetch_metadata(self, dois: List[str]) -> Optional[requests.Response]:
        return self._fetch_batch(META_BASE_URL, dois)

    def fetch_jats(self, dois: List[str]) -> Optional[requests.Response]:
        return self._fetch_batch(JATS_BASE_URL, dois)


def load_env_api_key() -> str:
    load_dotenv()
    key = os.environ.get("NATURE_API_KEY", "").strip()
    if not key:
        raise SystemExit("Error: NATURE_API_KEY not set or empty in .env")
    return key


def article_id_from_url(url: str) -> Optional[str]:
    url = (url or "").strip().rstrip("/")
    if "/articles/" in url:
        aid = url.split("/articles/")[-1].split("?")[0]
        return aid or None
    return None


def get_output_paths(article: Dict[str, Any], output_dir: str) -> Tuple[Optional[Path], Optional[Path]]:
    aid = article_id_from_url(article.get("url", ""))
    if not aid:
        return None, None
    year = year_from_date(article.get("date") or "")
    journal = path_safe_journal(article.get("journal") or "")
    base_path = Path(output_dir) / year / journal
    return base_path / f"{aid}_meta.json", base_path / f"{aid}.xml"


def is_nature_journal(article: Dict[str, Any]) -> bool:
    journal = (article.get("journal") or "").lower()
    if not journal:
        return False
    if journal.startswith("nature"):
        return True
    info = get_journal_info(journal)
    return info is not None and info.get("abbr") in ("nature", "ni")


def is_springer_article(article: Dict[str, Any]) -> bool:
    url = article.get("url", "")
    if bool(url) and ("nature.com" in url or "springer.com" in url):
        return True
    return is_nature_journal(article)


def _strip_ns(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def parse_jats_xml(text: str) -> Tuple[Dict[str, str], Set[str]]:
    result: Dict[str, str] = {}
    no_body_ids: Set[str] = Set()
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return result, no_body_ids

    records = next((elem for elem in root.iter() if _strip_ns(elem.tag) == "records"), None)
    if records is None:
        return result, no_body_ids

    for child in records:
        if _strip_ns(child.tag) != "article":
            continue
        
        aid = None
        for elem in child.iter():
            if _strip_ns(elem.tag) == "article-id" and elem.get("pub-id-type") == "publisher-id":
                aid = (elem.text or "").strip()
                break
        
        if not aid:
            continue
            
        has_body = any(_strip_ns(c.tag) == "body" for c in child)
        if not has_body:
            no_body_ids.add(aid)
            continue
            
        result[aid] = '<?xml version="1.0"?>\n' + ET.tostring(child, encoding="unicode")
    return result, no_body_ids


def process_batch(api: SpringerAPI, batch: List[Tuple[Dict[str, Any], Path, Path, str]]) -> Tuple[List[Dict], Set[str], bool]:
    id_to_meta_path = {article_id_from_url(a["url"]): mp for a, mp, _, _ in batch}
    id_to_xml_path = {article_id_from_url(a["url"]): xp for a, _, xp, _ in batch}
    id_to_doi = {article_id_from_url(a["url"]): d for a, _, _, d in batch}
    id_to_url = {article_id_from_url(a["url"]): a.get("url", "") for a, _, _, _ in batch}

    metadata_by_id: Dict[str, Dict] = {}
    oa_dois_to_fetch: List[str] = []
    dois_to_fetch_meta: List[str] = []

    # Round 1: Check local or prepare fetch
    for aid, mp in id_to_meta_path.items():
        if mp.exists():
            try:
                record = json.loads(mp.read_text(encoding="utf-8"))
                metadata_by_id[aid] = record
                if record.get("openaccess") == "true":
                    xp = id_to_xml_path.get(aid)
                    if xp and not xp.exists():
                        oa_dois_to_fetch.append(id_to_doi[aid])
            except (json.JSONDecodeError, IOError):
                dois_to_fetch_meta.append(id_to_doi[aid])
        else:
            dois_to_fetch_meta.append(id_to_doi[aid])

    # Fetch metadata if needed
    if dois_to_fetch_meta:
        r = api.fetch_metadata(dois_to_fetch_meta)
        if r is None:
            return [{"doi": d, "reason": "Metadata fetch failed"} for d in dois_to_fetch_meta], set(), False
        if r.status_code == 429:
            return [], set(), True
        if r.status_code == 200:
            records = r.json().get("records", [])
            for record in records:
                doi = record.get("doi")
                aid = next((a for a, d in id_to_doi.items() if d == doi), None)
                if aid:
                    metadata_by_id[aid] = record
                    mp = id_to_meta_path[aid]
                    mp.parent.mkdir(parents=True, exist_ok=True)
                    mp.write_text(json.dumps(record, indent=2), encoding="utf-8")
                    if record.get("openaccess") == "true":
                        xp = id_to_xml_path[aid]
                        if xp and not xp.exists():
                            oa_dois_to_fetch.append(doi)

    # Round 2: Fetch JATS if needed
    no_body_ids: Set[str] = set()
    if oa_dois_to_fetch:
        r = api.fetch_jats(oa_dois_to_fetch)
        if r is not None:
            if r.status_code == 429:
                return [], set(), True
            if r.status_code == 200:
                by_id, nb_ids = parse_jats_xml(r.text)
                no_body_ids.update(nb_ids)
                for aid, xml_content in by_id.items():
                    xp = id_to_xml_path.get(aid)
                    if xp:
                        xp.parent.mkdir(parents=True, exist_ok=True)
                        xp.write_text(xml_content, encoding="utf-8")

    # Collect failures
    failures = []
    for aid, doi in id_to_doi.items():
        if aid not in metadata_by_id:
            failures.append({"url": id_to_url.get(aid), "doi": doi, "reason": "Metadata not found"})
        elif metadata_by_id[aid].get("openaccess") == "true":
            xp = id_to_xml_path.get(aid)
            if xp and not xp.exists() and aid not in no_body_ids:
                failures.append({"url": id_to_url.get(aid), "doi": doi, "reason": "JATS XML missing"})

    return failures, no_body_ids, False


def main():
    parser = argparse.ArgumentParser(description="Download Springer Nature articles.")
    parser.add_argument("--limit", type=int, help="Limit number of articles.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Output directory.")
    parser.add_argument("--data-glob", nargs="*", help="Globs for metadata.")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help="Batch size.")
    args = parser.parse_args()

    api_key = load_env_api_key()
    api = SpringerAPI(api_key)
    
    data_glob = args.data_glob or DEFAULT_DATA_GLOB
    articles = load_articles(data_glob)
    if args.limit:
        articles = articles[:args.limit]

    logger.info(f"Found {len(articles)} articles.")

    to_fetch = []
    seen_dois = set()
    stats = {}
    already_exists = 0

    for article in articles:
        if not is_springer_article(article) or not is_nature_journal(article):
            continue
            
        doi = (article.get("doi") or "").strip()
        aid = article_id_from_url(article.get("url", ""))
        if not doi and aid and aid.startswith("s"):
            doi = f"10.1038/{aid}"
        
        if not doi or doi in seen_dois:
            continue
        seen_dois.add(doi)

        journal = article.get("journal", "Unknown")
        if journal not in stats:
            stats[journal] = {"found": 0, "processed": 0, "saved": 0, "failed": 0, "exists": 0}
        stats[journal]["found"] += 1

        mp, xp = get_output_paths(article, args.output_dir)
        if not mp:
            stats[journal]["failed"] += 1
            continue

        # Quick skip check
        is_oa = False
        if mp.exists():
            try:
                is_oa = json.loads(mp.read_text(encoding="utf-8")).get("openaccess") == "true"
            except: pass
        
        if mp.exists() and (not is_oa or (xp and xp.exists())):
            already_exists += 1
            stats[journal]["exists"] += 1
            continue

        to_fetch.append((article, mp, xp, doi))
        stats[journal]["processed"] += 1

    logger.info(f"Processing {len(to_fetch)} articles ({already_exists} already exist).")

    batches = [to_fetch[i:i + args.batch_size] for i in range(0, len(to_fetch), args.batch_size)]
    doi_to_journal = {doi: a.get("journal", "Unknown") for a, _, _, doi in to_fetch}
    
    failures = []
    no_body_ids = set()
    stop_requested = False

    with tqdm(total=len(to_fetch), desc="Downloading") as pbar:
        with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as executor:
            future_to_batch = {executor.submit(process_batch, api, b): b for b in batches}
            for future in as_completed(future_to_batch):
                if stop_requested:
                    future.cancel()
                    continue
                try:
                    batch_failures, batch_no_body, stop = future.result()
                    failures.extend(batch_failures)
                    no_body_ids.update(batch_no_body)
                    
                    for f in batch_failures:
                        j = doi_to_journal.get(f.get("doi"), "Unknown")
                        if j in stats: stats[j]["failed"] += 1
                    
                    if stop:
                        stop_requested = True
                        executor.shutdown(wait=False, cancel_futures=True)
                except Exception as e:
                    logger.error(f"Batch failed: {e}")
                pbar.update(len(future_to_batch[future]))

    # Final Stats
    logger.info("\n--- Stats ---")
    header = f"{'Journal':<40} {'Found':<8} {'ToProc':<8} {'Saved':<8} {'Failed':<8} {'Exist':<8}"
    print(header)
    print("-" * len(header))
    for j, s in sorted(stats.items()):
        s["saved"] = s["processed"] - s["failed"]
        print(f"{j[:39]:<40} {s['found']:<8} {s['processed']:<8} {s['saved']:<8} {s['failed']:<8} {s['exists']:<8}")

    if failures:
        with open("noresponse.log", "a") as f:
            for entry in failures:
                f.write(json.dumps(entry) + "\n")
    if no_body_ids:
        with open("nobody.log", "a") as f:
            for aid in sorted(no_body_ids):
                f.write(f"{aid}\n")


if __name__ == "__main__":
    setup_proxy()
    main()

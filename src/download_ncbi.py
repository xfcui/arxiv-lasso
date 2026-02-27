"""Download full-text XML articles from PubMed Central (PMC) via NCBI E-Utilities."""

import calendar
import datetime
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import socks
import socket
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()

# Configure SOCKS proxy if environment variables are set
proxy_url = os.getenv("ALL_PROXY")
if proxy_url:
    try:
        parsed = urllib.parse.urlparse(proxy_url)
        if parsed.scheme in ("socks5", "socks5h"):
            proxy_host = parsed.hostname or "localhost"
            proxy_port = parsed.port or 1080
            rdns = parsed.scheme == "socks5h"
            
            socket.setdefaulttimeout(600)
            socks.set_default_proxy(socks.SOCKS5, proxy_host, proxy_port, rdns=rdns)
            socket.socket = socks.socksocket
            print(f"Proxy configured: {proxy_host}:{proxy_port} (rdns={rdns})")
        else:
            print(f"Warning: Unsupported proxy protocol: {parsed.scheme}. Use socks5/socks5h.")
    except Exception as e:
        print(f"Error configuring proxy: {e}")

# NCBI E-Utilities URLs
ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

# Configuration
API_KEY = os.getenv("NCBI_API_KEY")
EMAIL = os.getenv("NCBI_EMAIL", "your.email@example.com")
TOOL = "arxiv-ncbi"

DB = "pmc"
SEARCH_RESULT_LIMIT = 9999  # NCBI caps at 9999; split date range when count >= this
BATCH_SIZE = 30
MAX_THREADS = 12
API_TIMEOUT = 300
MAX_RETRIES = 3
REQUEST_DELAY = 1
INITIAL_RETRY_DELAY = 2

# Hardcoded journal list (NLM Title Abbreviation)
JOURNALS = [
    "Sci Adv",
    "Proc Natl Acad Sci U S A",
    "Genome Biol",
    "Nucleic Acids Res",
    "Bioinformatics",
    "Brief Bioinform",
    "PLoS Comput Biol",
]


class DownloadError(Exception):
    """Raised when an article download or search fails."""

    pass


def _log_api_key_status() -> None:
    """Log whether NCBI API key is configured (call once at startup)."""
    if not API_KEY:
        tqdm.write("NCBI API KEY not set. Rate limits will be more restrictive.")


def _build_ncbi_params(extra: Dict[str, Any]) -> Dict[str, Any]:
    """Build NCBI E-Utilities params with common fields.

    Args:
        extra: Additional parameters for the NCBI request.

    Returns:
        A dictionary containing the full set of parameters.
    """
    params = {"db": DB, "email": EMAIL, "tool": TOOL, **extra}
    if API_KEY:
        params["api_key"] = API_KEY
    return params


def _fetch_url_with_retry(
    url: str,
    *,
    max_retries: int = MAX_RETRIES,
    retry_delay: float = INITIAL_RETRY_DELAY,
    context: str = "",
) -> Optional[bytes]:
    """Fetch URL with exponential backoff on HTTP 429.

    Args:
        url: The URL to fetch.
        max_retries: Maximum number of retry attempts.
        retry_delay: Initial delay between retries.
        context: Contextual information for error logging.

    Returns:
        The response content as bytes, or None if the request failed.
    """
    delay = retry_delay
    for _ in range(max_retries):
        try:
            with urllib.request.urlopen(url, timeout=API_TIMEOUT) as response:
                data = response.read()
            time.sleep(REQUEST_DELAY)
            return data
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(delay)
                delay *= 2
            else:
                tqdm.write(f"HTTP Error{f' ({context})' if context else ''}: {e}")
                return None
        except Exception as e:
            tqdm.write(f"Error{f' ({context})' if context else ''}: {e}")
            return None
    return None


def _normalize_pmcid(pmcid: str) -> str:
    """Ensure PMCID has PMC prefix.

    Args:
        pmcid: The PMCID string to normalize.

    Returns:
        The normalized PMCID string.
    """
    return pmcid if pmcid.startswith("PMC") else f"PMC{pmcid}"


def _build_date_query(
    start_year: int,
    end_year: int,
    start_month: Optional[int] = None,
    end_month: Optional[int] = None,
) -> str:
    """Build date range for [pdat] query. Uses YYYY/MM/DD when splitting by half-year.

    Args:
        start_year: The start year of the range.
        end_year: The end year of the range.
        start_month: The optional start month.
        end_month: The optional end month.

    Returns:
        A formatted date query string for NCBI.
    """
    if start_month is not None and end_month is not None:
        start_d = f"{start_year}/{start_month:02d}/01"
        # Last day of end_month
        if end_month == 12:
            end_d = f"{end_year}/12/31"
        else:
            last = calendar.monthrange(end_year, end_month)[1]
            end_d = f"{end_year}/{end_month:02d}/{last}"
        return f'("{start_d}"[pdat] : "{end_d}"[pdat])'
    return f"({start_year}:{end_year}[pdat])"


def _search_articles_impl(
    journal: str,
    start_year: int,
    end_year: int,
    start_month: Optional[int],
    end_month: Optional[int],
    show_pbar: bool = True,
) -> List[str]:
    """Internal search with optional month range. Splits into half-years when count >= 10k.

    Args:
        journal: The journal name to search in.
        start_year: The start year.
        end_year: The end year.
        start_month: The optional start month.
        end_month: The optional end month.
        show_pbar: Whether to show a progress bar.

    Returns:
        A list of PMCIDs found.
    """
    query = f'"{journal}"[Journal] AND {_build_date_query(start_year, end_year, start_month, end_month)}'
    all_ids: List[str] = []
    retstart = 0
    pbar = None

    while True:
        params = _build_ncbi_params(
            {"term": query, "retmode": "json", "retmax": 10000, "retstart": retstart}
        )
        url = f"{ESEARCH_URL}?{urllib.parse.urlencode(params)}"
        raw = _fetch_url_with_retry(url, context=f"search {journal}")
        if raw is None:
            break

        try:
            data = json.loads(raw.decode())
        except json.JSONDecodeError as e:
            tqdm.write(f"Error parsing search response for {journal}: {e}")
            break

        result = data.get("esearchresult", {})
        idlist = result.get("idlist", [])
        count = int(result.get("count", 0))
        all_ids.extend(idlist)
        retstart += len(idlist)

        if pbar is None and show_pbar:
            pbar = tqdm(total=count, desc=f"Searching {journal}", unit="ids", leave=False)
        if pbar is not None:
            pbar.update(len(idlist))
            pbar.n = min(pbar.n, count)
            pbar.refresh()

        # NCBI caps at 10k results; split date range when we hit the limit
        if count >= SEARCH_RESULT_LIMIT and start_year == end_year:
            if pbar is not None:
                pbar.close()
            if start_month is None:
                # Full year: split into H1 and H2
                h1 = _search_articles_impl(journal, start_year, end_year, 1, 6, show_pbar=False)
                h2 = _search_articles_impl(journal, start_year, end_year, 7, 12, show_pbar=False)
            elif end_month - start_month >= 2:
                # Half-year or more: split into two quarters
                mid = (start_month + end_month) // 2
                h1 = _search_articles_impl(
                    journal, start_year, end_year, start_month, mid, show_pbar=False
                )
                h2 = _search_articles_impl(
                    journal, start_year, end_year, mid + 1, end_month, show_pbar=False
                )
            else:
                # Single month with >10k - cannot split further; return what we got
                break
            seen = set()
            merged = []
            for pid in h1 + h2:
                if pid not in seen:
                    seen.add(pid)
                    merged.append(pid)
            return merged

        if retstart >= count or not idlist:
            break

    if pbar is not None:
        pbar.close()
    return all_ids


def search_articles(journal: str, start_year: int, end_year: int) -> List[str]:
    """Search for PMCIDs in a journal within a year range.

    Args:
        journal: The journal name.
        start_year: The start year.
        end_year: The end year.

    Returns:
        A list of PMCIDs found.
    """
    return _search_articles_impl(journal, start_year, end_year, None, None)


def fetch_metadata_json(pmcids: List[str]) -> Dict[str, Any]:
    """Fetch metadata in JSON format for multiple PMCIDs.

    Args:
        pmcids: A list of PMCIDs.

    Returns:
        A dictionary containing the metadata.
    """
    params = _build_ncbi_params({"id": ",".join(pmcids), "retmode": "json"})
    url = f"{ESUMMARY_URL}?{urllib.parse.urlencode(params)}"
    raw = _fetch_url_with_retry(url, context="metadata")
    if raw is None:
        return {}
    try:
        return json.loads(raw.decode())
    except json.JSONDecodeError as e:
        tqdm.write(f"Error parsing metadata JSON: {e}")
        return {}


def _extract_pmcid(article: ET.Element) -> Optional[str]:
    """Extract PMCID from article XML (handles namespaced tags).

    Args:
        article: The XML element representing the article.

    Returns:
        The PMCID string, or None if not found.
    """
    for elem in article.iter():
        if elem.tag.endswith("article-id") and elem.get("pub-id-type") in ("pmc", "pmcid"):
            if elem.text:
                pmcid = elem.text
                return pmcid if pmcid.startswith("PMC") else f"PMC{pmcid}"
            return None
    return None


def _parse_pub_date(article: ET.Element) -> Tuple[str, str]:
    """Parse publication date from article XML. Returns (year, month).

    Args:
        article: The XML element representing the article.

    Returns:
        A tuple of (year, month) as strings.
    """
    for pub_type in (
        ".//pub-date[@pub-type='epub']",
        ".//pub-date[@pub-type='ppub']",
        ".//pub-date[@publication-format='electronic'][@date-type='pub']",
        ".//pub-date[@pub-type='collection']",
    ):
        pub_date = article.find(pub_type)
        if pub_date is not None:
            year = "0000"
            month = "00"
            if (y := pub_date.find("year")) is not None and y.text:
                year = y.text
            if (m := pub_date.find("month")) is not None and m.text:
                month = m.text.zfill(2)
            return year, month
    return "0000", "00"


def _save_article(
    article: ET.Element,
    result_meta: Dict[str, Any],
    journal: str,
) -> None:
    """Save a single article as XML and JSON metadata.

    Args:
        article: The XML element representing the article.
        result_meta: Metadata dictionary for the article.
        journal: The journal name.
    """
    pmcid = _extract_pmcid(article)
    if not pmcid:
        return

    numeric_id = pmcid.replace("PMC", "")
    article_metadata = result_meta.get(numeric_id, {})
    year, month = _parse_pub_date(article)

    dir_path = Path("data/ncbi") / f"{year}{month}" / journal.replace(" ", "_")
    dir_path.mkdir(parents=True, exist_ok=True)

    xml_path = dir_path / f"{pmcid}.xml"
    if xml_path.exists():
        return

    meta_path = dir_path / f"{pmcid}_meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(article_metadata, f, indent=2)

    with open(xml_path, "wb") as f:
        f.write(ET.tostring(article, encoding="utf-8"))


def fetch_and_save_articles(pmcids: List[str], journal: str) -> None:
    """Fetch full-text XML and metadata for PMCIDs and save as XML/JSON.

    Args:
        pmcids: A list of PMCIDs.
        journal: The journal name.
    """
    pmcid_list = ",".join(pmcids)
    result_meta = fetch_metadata_json(pmcids).get("result", {})

    params = _build_ncbi_params({"id": pmcid_list, "retmode": "xml"})
    url = f"{EFETCH_URL}?{urllib.parse.urlencode(params)}"
    raw = _fetch_url_with_retry(url, context="efetch")
    if raw is None:
        return

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        tqdm.write(f"Error parsing batch XML: {e}")
        return

    articles = root.findall("article")
    if not articles and root.tag == "article":
        articles = [root]
    if not articles:
        tqdm.write(f"Warning: No articles found in batch response for {pmcid_list}")
        return

    for article in articles:
        _save_article(article, result_meta, journal)


def _collect_existing_pmcids(articles_dir: Path) -> Set[str]:
    """Build set of PMCIDs that already have XML files.

    Args:
        articles_dir: The directory to search for existing articles.

    Returns:
        A set of existing PMCIDs.
    """
    existing = set()
    if not articles_dir.exists():
        return existing
    for path in articles_dir.rglob("PMC*.xml"):
        existing.add(path.stem)
    return existing


def process_journal_for_year(
    journal: str,
    year: int,
    existing_pmcids: Set[str],
) -> int:
    """Search and download articles for one journal in one year.

    Args:
        journal: The journal name.
        year: The year to process.
        existing_pmcids: A set of already downloaded PMCIDs.

    Returns:
        The number of newly downloaded articles.
    """
    tqdm.write(f"Processing {journal} for {year}...")

    pmcids = search_articles(journal, year, year)
    if not pmcids:
        return 0

    to_download = [p for p in pmcids if _normalize_pmcid(p) not in existing_pmcids]
    tqdm.write(f"- Found {len(to_download)}/{len(pmcids)} articles to download")
    if not to_download:
        return 0

    batches = [to_download[i : i + BATCH_SIZE] for i in range(0, len(to_download), BATCH_SIZE)]
    newly_downloaded = 0

    with tqdm(total=len(to_download), desc=f"  {journal}", unit="articles", leave=False) as pbar:

        def download_batch(batch: List[str]) -> int:
            try:
                fetch_and_save_articles(batch, journal)
                pbar.update(len(batch))
                # Note: existing_pmcids is updated in the main thread or protected if needed.
                # Since we are just adding to a set, and Python's set.add is thread-safe (GIL),
                # and we are only using it for skipping already downloaded ones in the next year/journal,
                # this is generally safe for this specific use case.
                for p in batch:
                    existing_pmcids.add(_normalize_pmcid(p))
                return len(batch)
            except Exception as e:
                pbar.set_postfix_str(f"Error: {e}", refresh=True)
                return 0

        with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
            results = list(executor.map(download_batch, batches))
            newly_downloaded = sum(results)

    to_download = [p for p in pmcids if _normalize_pmcid(p) not in existing_pmcids]
    tqdm.write(f"- Found {len(to_download)}/{len(pmcids)} articles remaining")

    return newly_downloaded


def main() -> None:
    """Entry point: download articles for configured journals and year range."""
    _log_api_key_status()

    journals = JOURNALS
    if not journals:
        tqdm.write("No journals to process. Exiting.")
        return

    today = datetime.date.today()
    current_year = today.year
    start_year = current_year - 1

    existing_pmcids = _collect_existing_pmcids(Path("data/ncbi"))
    initial_count = len(existing_pmcids)

    total_downloaded = 0
    journal_stats: dict[str, dict[str, int]] = {}

    for year in range(current_year, start_year - 1, -1):
        # Track unique IDs found this year to avoid double counting across journals
        # (though NCBI journals are usually distinct, it's good practice)
        for journal in journals:
            if journal not in journal_stats:
                journal_stats[journal] = {"downloaded": 0}
            
            downloaded = process_journal_for_year(journal, year, existing_pmcids)
            total_downloaded += downloaded
            journal_stats[journal]["downloaded"] += downloaded

    print(f"\n--- NCBI Stats ---")
    print(f"{'Journal':<40} {'Downloaded':<12}")
    print("-" * 55)
    for journal in sorted(journal_stats.keys()):
        s = journal_stats[journal]
        print(f"{journal[:39]:<40} {s['downloaded']:<12}")
    print("-" * 55)

    final_count = len(existing_pmcids)
    tqdm.write(f"Done. Downloaded: {total_downloaded} | Total on disk: {final_count} (was {initial_count})")


if __name__ == "__main__":
    main()


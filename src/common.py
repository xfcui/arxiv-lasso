"""Shared utilities for metadata loading and path helpers."""
from __future__ import annotations

import glob
import json
import os
import re
import socket
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

import socks
from dotenv import load_dotenv

from config import get_journal_info


def setup_proxy() -> None:
    """Configure global proxy if ALL_PROXY is set in .env."""
    load_dotenv()
    proxy_url = os.getenv("ALL_PROXY")
    if proxy_url:
        parsed = urllib.parse.urlparse(proxy_url)
        if parsed.scheme in ("socks5", "socks5h"):
            socks.set_default_proxy(
                socks.SOCKS5,
                parsed.hostname or "localhost",
                parsed.port or 1080,
                rdns=(parsed.scheme == "socks5h")
            )
            socket.socket = socks.socksocket


def parse_publication_date(s: str) -> datetime | None:
    """Parse a publication date string; return datetime or None."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    # Added %Y/%m/%d and %Y.%m.%d to support more formats
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%b %d, %Y", "%b %Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def year_from_date(date_str: str) -> str:
    """Extract the year component from a date string.
    
    Example: '2026-02-07' → '2026', 'Jan 06, 2022' -> '2022'.
    """
    if not date_str:
        return "0000"
    dt = parse_publication_date(date_str)
    if dt:
        return str(dt.year)
    # Handle '2026-02-07' or '2026/02/07'
    match = re.search(r"\b(19|20)\d{2}\b", str(date_str))
    if match:
        return match.group(0)
    return "0000"


def path_safe_journal(journal: str) -> str:
    """Sanitize a journal name for use as a filesystem path component."""
    if not journal:
        return "Unknown"
    
    info = get_journal_info(journal)
    if info:
        return info["path_name"]
    
    s = re.sub(r"[^\w\s-]", "", journal)
    s = re.sub(r"[-\s]+", "_", s).strip("_")
    return s or "Unknown"


def load_articles(data_glob: str | tuple[str, ...]) -> list[dict[str, Any]]:
    """
    Glob JSON metadata files, merge all article entries, and deduplicate by URL.

    Each JSON file must have an ``articles`` key whose value is either a dict
    (keyed by article ID, the new format) or a list (legacy format).
    """
    patterns = (data_glob,) if isinstance(data_glob, str) else data_glob
    
    paths: list[Path] = []
    for pat in patterns:
        # Use Path.glob for recursive search if needed, or just glob.glob
        # Since the input is a glob string, we can use Path('.').glob(pat)
        paths.extend(Path(".").glob(pat))
    
    paths = sorted(set(paths))
    if not paths:
        raise SystemExit(f"Error: No files matched {patterns}")

    seen_urls: set[str] = set()
    articles: list[dict[str, Any]] = []

    for path in paths:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)

        # Handle top-level metadata (journal, publicationDate) for articles within
        top_journal = data.get("journal")
        top_date = data.get("publicationDate") or data.get("pubdate") or data.get("date")

        articles_raw = data.get("articles", data) if isinstance(data, dict) else data
        items: list[dict[str, Any]] = (
            list(articles_raw.values())
            if isinstance(articles_raw, dict)
            else (articles_raw if isinstance(articles_raw, list) else [articles_raw])
        )

        for item in items:
            url = item.get("url")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            
            # Inherit top-level metadata if missing in item
            if top_journal and not item.get("journal"):
                item["journal"] = top_journal
            
            # Use item's own publicationDate or date if available, otherwise use top_date
            item_date = item.get("publicationDate") or item.get("date")
            if not item_date:
                item["date"] = top_date
            else:
                item["date"] = item_date
                
            articles.append(item)

    return articles

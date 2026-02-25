#!/usr/bin/env python
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import feedparser

from common import setup_proxy

# List of RSS feeds provided
FEEDS: dict[str, str] = {
    "Nature": "https://www.nature.com/nature.rss",
    "Nature Immunology": "https://www.nature.com/ni.rss",
    "Nature Methods": "https://www.nature.com/nmeth.rss",
    "Nature Biotechnology": "https://www.nature.com/nbt.rss",
    "Nature Machine Intelligence": "https://www.nature.com/natmachintell.rss",
    "Nature Computational Science": "https://www.nature.com/natcomputsci.rss",
    "Science": "https://www.science.org/action/showFeed?type=etoc&feed=rss&jc=science",
    "Science Immunology": "https://www.science.org/action/showFeed?type=etoc&feed=rss&jc=sciimmunol",
    "Cell": "https://www.cell.com/cell/inpress.rss",
    "Cell Immunity": "https://www.cell.com/immunity/inpress.rss"
}


def extract_doi(entry: Any) -> str:
    """Extract DOI from various fields in the RSS entry.
    
    Args:
        entry: The RSS feed entry.
        
    Returns:
        The extracted DOI string, or an empty string if not found.
    """
    # 1. Check for 'prism_doi' (Nature, Science)
    if 'prism_doi' in entry:
        return str(entry.prism_doi)
    
    # 2. Check for 'dc_identifier' (Cell, Nature, Science)
    # Often in format "doi:10.xxxx/yyyy" or just "10.xxxx/yyyy"
    if 'dc_identifier' in entry:
        doi = str(entry.dc_identifier)
        if doi.lower().startswith("doi:"):
            return doi[4:]
        return doi

    # 3. Try to extract from link or id (Science/Nature often have DOI in URL)
    # Look for the 10.xxxx/yyyy pattern
    for field in ["link", "id"]:
        val = entry.get(field, "")
        match = re.search(r'10\.\d{4,9}/[-._;()/:A-Z0-9]+', val, re.I)
        if match:
            return match.group(0)

    # 4. Try to extract from summary/description (Nature fallback)
    summary = entry.get("summary", "")
    doi_match = re.search(r'doi:(10\.\d{4,9}/[-._;()/:A-Z0-9]+)', summary, re.I)
    if doi_match:
        return doi_match.group(1)
        
    return ""


def extract_date(entry: Any, journal_name: str) -> str:
    """Extract and format the date from the entry based on journal patterns.
    
    Args:
        entry: The RSS feed entry.
        journal_name: The name of the journal.
        
    Returns:
        The extracted date string, or an empty string if not found.
    """
    # 1. Check for 'updated' (Nature/Science/Cell seem to use this for the date)
    if 'updated' in entry and entry.updated:
        # Some are YYYY-MM-DD, some are YYYY-MM-DDTHH:MM:SSZ
        return str(entry.updated.split('T')[0])
    
    # 2. Check for 'published' (Standard RSS)
    if 'published' in entry and entry.published:
        return str(entry.published)

    # 3. Pattern matching from summary if needed (Nature often has it in text)
    summary = entry.get("summary", "")
    if journal_name.startswith("Nature"):
        # Pattern: "Published online: 23 February 2026"
        match = re.search(r'Published online: (\d{1,2} \w+ \d{4})', summary)
        if match:
            return match.group(1)
            
    if journal_name == "Science":
        # Pattern: "February 2026"
        match = re.search(r'([A-Z][a-z]+ \d{4})', summary)
        if match:
            return match.group(1)

    return ""


def download_rss_metadata(feeds: dict[str, str], base_output_dir: str = "metadata") -> None:
    """Download metadata from RSS feeds and save to JSON.
    
    Args:
        feeds: A dictionary mapping journal names to RSS feed URLs.
        base_output_dir: The base directory to save metadata files.
    """
    all_articles: dict[str, dict[str, Any]] = {}
    stats: dict[str, int] = {}
    
    # Generate year and month-day for the directory and filename
    now = datetime.now()
    year = now.strftime("%Y")
    mmdd = now.strftime("%m%d")
    
    # Ensure year-based output directory exists
    output_dir = Path(base_output_dir) / year
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Ensured directory exists: {output_dir}")

    for journal_name, url in feeds.items():
        print(f"Fetching metadata for {journal_name}...")
        try:
            # Parse the RSS feed
            feed = feedparser.parse(url)
            
            # Check for parsing errors
            if feed.bozo:
                print(f"  Warning: Potential issue parsing {journal_name}: {feed.bozo_exception}")
            
            count = 0
            for entry in feed.entries:
                # Extract DOI
                doi = extract_doi(entry)
                
                # Extract Date
                article_date = extract_date(entry, journal_name)
                
                # Clean URL: remove everything after '?'
                raw_url = entry.get("link", "")
                clean_url = raw_url.split('?')[0]
                
                # Extract article ID: last token after '/' in URL
                article_id = clean_url.rstrip('/').split('/')[-1]
                
                # Filter out non-research articles for Nature (IDs starting with 'd')
                if journal_name.startswith("Nature") and article_id.startswith('d'):
                    continue
                
                # Filter out correction articles
                title = entry.get("title", "")
                if any(kw in title for kw in ["Correction:", "Author Correction", "Publisher Correction", "Erratum"]):
                    continue
                
                article = {
                    "title": entry.get("title", ""),
                    "journal": journal_name,
                    "date": article_date,
                    "author": entry.get("author", ""),
                    "url": clean_url,
                    "doi": doi
                }
                all_articles[article_id] = article
                count += 1
            
            stats[journal_name] = count
            print(f"  Successfully downloaded {count} articles from {journal_name}.")
            
            # Be polite to the servers
            time.sleep(1)
            
        except Exception as e:
            print(f"  Error fetching {journal_name}: {e}")

    # Generate filename based on current date (mmdd.json)
    output_file = output_dir / f"{mmdd}.json"

    # Final JSON structure with metadata combined at the top
    output_data = {
        "metadata": {
            "timestamp": datetime.now().isoformat(),
            "journal_stats": stats,
            "total_articles": len(all_articles)
        },
        "articles": all_articles
    }

    # Save to JSON file
    with output_file.open("w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=4, ensure_ascii=False)
    
    print(f"\nTotal articles saved: {len(all_articles)} to {output_file}")


if __name__ == "__main__":
    setup_proxy()
    download_rss_metadata(FEEDS)

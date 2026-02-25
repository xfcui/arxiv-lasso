#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from common import load_articles, path_safe_journal, year_from_date
from config import get_journal_info


def convert_science_urls(data_glob: str | tuple[str, ...], output_file: str) -> None:
    """Convert Science article URLs to PDF URLs for aria2c.
    
    Args:
        data_glob: Glob(s) for metadata JSON files.
        output_file: Output file path for aria2c input.
    """
    articles = load_articles(data_glob)
    aria2_lines: list[str] = []

    for info in articles:
        journal = info.get("journal", "")
        journal_info = get_journal_info(journal)
        is_science = journal.startswith("Science") or (journal_info and journal_info["abbr"] in ("science", "sciimmunol"))
        
        # Check if it's a Science family journal
        if is_science:
            doi = info.get("doi", "")
            url = info.get("url", "")
            pdf_url: str | None = None
            
            if doi:
                # Standard Science PDF pattern: https://www.science.org/doi/pdf/10.1126/xxx
                pdf_url = f"https://www.science.org/doi/pdf/{doi}"
            else:
                # Fallback to URL manipulation if DOI is missing
                if "science.org/doi/abs/" in url:
                    pdf_url = url.replace("/doi/abs/", "/doi/pdf/")
                elif "science.org/doi/" in url and "/pdf/" not in url:
                    pdf_url = url.replace("/doi/", "/doi/pdf/")
            
            if pdf_url:
                # Get path components for output filename and directory
                year = year_from_date(info.get("date") or "")
                journal_path = path_safe_journal(journal)
                
                # Derive filename from DOI or URL
                if doi:
                    filename = f"{doi.replace('/', '_')}.pdf"
                else:
                    filename = f"{url.split('/')[-1].split('?')[0]}.pdf"
                
                out_dir = Path("pdf") / year / journal_path
                
                # aria2c input file format:
                # URL
                #   dir=<directory>
                #   out=<filename>
                aria2_lines.append(pdf_url)
                aria2_lines.append(f"  dir={out_dir}")
                aria2_lines.append(f"  out={filename}")

    out_path = Path(output_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open('w', encoding="utf-8") as f:
        for line in aria2_lines:
            f.write(line + '\n')

    print(f"Successfully converted {len(aria2_lines)//3} Science article URLs to aria2c format.")
    print(f"Saved to {output_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert Science article URLs to PDF URLs.")
    parser.add_argument(
        "--data-glob", type=str, nargs="*", 
        default=["metadata/2026/0224.json", "chrome/**/*.json"],
        help="Glob(s) for metadata JSON files."
    )
    parser.add_argument(
        "--output", type=str, default="chrome/sci_urls.txt",
        help="Output file for PDF URLs (default: chrome/sci_urls.txt)."
    )
    args = parser.parse_args()
    
    convert_science_urls(tuple(args.data_glob), args.output)

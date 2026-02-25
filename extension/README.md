# Academic Journal Scraper Chrome Extension

A Chrome extension to automate the extraction of research article metadata from Cell Press, Science, and Nature journal pages. It supports all Cell journals (e.g., Cell, Immunity, Cell Stem Cell, Neuron), Science journals (e.g., Science, Science Immunology, Science Translational Medicine), and Nature search pages. It navigates through "Next Issue" links (Cell/Science) or pagination (Nature) and saves both the extracted metadata and the raw HTML of each page for analysis.

## Features

- **Multi-Journal Support**: Works with `cell.com`, `science.org`, and `nature.com` platforms.
- **Automated Navigation**: Automatically follows "Next Issue" links (Cell/Science) or pagination (Nature, up to 20 pages) until the end of the series.
- **Article Extraction**: Collects research article titles, URLs, and Open Access status from each issue or search page.
- **Unified Data Format**: Outputs data in a consistent JSON schema with `type` discriminator for easy processing.
- **Background Processing**: Runs in a separate, non-intrusive background tab.
- **Debug Archives**: Saves the raw HTML of every visited page to your Downloads folder.
- **Real-time Progress**: View the number of issues/pages and articles processed in the extension popup.
- **Research-Only Filtering**: Automatically excludes reviews, news, and commentaries to maintain dataset quality.

## Installation

1.  Open Chrome and navigate to `chrome://extensions/`.
2.  Enable **Developer mode** using the toggle in the top right corner.
3.  Click the **Load unpacked** button.
4.  Select the `extension` folder from this project directory.

## How to Use

1.  Navigate to any supported journal page:
    - **Cell**: `https://www.cell.com/cell/issue` or `https://www.cell.com/immunity/issue`
    - **Science**: `https://www.science.org/toc/science/391/6785` or `https://www.science.org/toc/sciimmunol/11/125`
    - **Nature**: `https://www.nature.com/search?q=immunology&journal=nature-immunology&date_range=2026`
2.  Click the **Journal Scraper** icon in your Chrome toolbar.
3.  Click **Start Extraction**.
4.  The extension will open a new tab and begin processing. You can monitor progress in the popup.
5.  Once finished, or if you click **Stop**, the extension will:
    - **Cell/Science**: Save individual issue JSON and HTML to `Downloads/chrome/{journal}/{yyyy}/{mmdd}.json` and `.html`.
    - **Nature**: Save each search page as JSON and HTML to `Downloads/chrome/search/{yyyymmdd}/page{N}.json` and `.html`.

## Output Format

The extension outputs data in a unified JSON schema:

- **Issue type** (Cell/Science): `{ type: "issue", journal, publicationDate, scrapedAt, articles: [{title, url, isOA}] }`
- **Search Page type** (Nature): `{ type: "search_page", scrapedAt, articles: [{title, url, journal, publicationDate, isOA}] }`

## Output Location

Due to Chrome's security model, all files are saved relative to your default **Downloads** folder:
- **Issue Files** (Cell/Science): `~/Downloads/chrome/{journal}/{yyyy}/{mmdd}.json` and `.html`
- **Search Page Files** (Nature): `~/Downloads/chrome/search/{yyyymmdd}/page{N}.json` and `.html`

## Technical Details

- **Manifest V3**: Built using the latest Chrome extension standards.
- **Permissions**: Uses `tabs`, `storage`, `downloads`, and `activeTab`.
- **Filtering Policy**: Follows the [Research-Only Policy](../.cursor/rules/research-only.mdc).
- **Selectors (Cell)**:
  - Articles Section: `h2.toc__heading__header` (Text: "Articles")
  - Next Issue: `a.content-navigation__btn--next`
- **Selectors (Science)**:
  - Articles Section: `h4.to-section, h5.to-section` (Filtering for "Research Article")
  - Next Issue: `a.content-navigation__btn--next` or `a[title="Next"]`
- **Selectors (Nature)**:
  - Article containers: `article.c-card, li[data-test="article-item"]`
  - Pagination: Automatically follows up to 20 pages

## Data Processing Pipeline

The extension is part of a larger planned workflow:

1. **Chrome Extension** → Scrapes and saves to `~/Downloads/chrome/...`
2. **`refactor_metadata.py`** (Planned) → Groups articles, deduplicates, outputs to `metadata/`
3. **`download_articles.py`** (Planned) → Downloads full-text XML from Elsevier API to `xml/`

See the [main README](../README.md) for project status and future plans.

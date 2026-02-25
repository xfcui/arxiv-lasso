# 📄 Arxiv Lasso

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Arxiv Lasso is a comprehensive toolset for automated extraction and downloading of research article metadata and full-text XML from major academic platforms, including Cell Press, Science, and Nature.

✨ **Key Features**
- 🌐 **Multi-Platform Support**: Extract metadata from `cell.com`, `science.org`, and `nature.com`.
- 🧩 **Chrome Extension**: Automated browser-based scraping for complex journal pages.
- 📡 **RSS Integration**: Real-time metadata tracking via journal RSS feeds.
- 📥 **Full-Text Downloader**: Automated XML retrieval from Elsevier (Cell) and Springer Nature (Nature) APIs.
- 🧬 **NCBI/PMC Support**: Bulk download of open-access articles from PubMed Central.
- 🛡️ **Proxy Support**: Built-in SSH tunnel management for institutional access.

---

## 🚀 Quick Start

### 1. Installation
```bash
git clone https://github.com/your-repo/arxiv-lasso.git
cd arxiv-lasso
pip install -r requirements.txt
```

### 2. Configuration
Create a `.env` file in the root directory:
```text
ELSEVIER_API_KEY=your_elsevier_key
NATURE_API_KEY=your_nature_key
NCBI_API_KEY=your_ncbi_key
ALL_PROXY=socks5h://localhost:1080
```

### 3. Basic Usage
Run the integrated download sequence:
```bash
./src/download_proxy.sh
```

---

## 🛠️ Components

### Chrome Extension
Located in `extension/`, this allows you to scrape metadata directly from your browser.
1. Load `extension/` as an unpacked extension in Chrome.
2. Navigate to a journal issue or search page.
3. Click **Start Extraction** in the popup.

### Python Scripts (`src/`)
- `download_rss.py`: Fetches latest articles from configured RSS feeds.
- `download_elsevier.py`: Downloads full-text XML for Cell/Elsevier articles.
- `download_springer.py`: Downloads JATS XML for Nature/Springer articles.
- `download_ncbi.py`: Searches and downloads from PubMed Central.
- `convert_science_urls.py`: Prepares Science.org URLs for batch downloading.

---

## 📁 Project Structure

- `src/`: Core Python logic and downloaders.
- `extension/`: Chrome extension for browser-based scraping.
- `metadata/`: Stored article metadata (JSON).
- `data/`: Downloaded full-text XML and PDFs.
- `metadata/`: Year-based metadata storage.

---

## 📜 License
This project is licensed under the MIT License.

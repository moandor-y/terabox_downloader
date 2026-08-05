# 🚀 terabox-downloader (`terabox-dl`)

An automated, concurrent, and resumable file downloader for TeraBox shared links using **1024teradl.com** and browser automation.

---

## 🌟 Key Features

1. **Automated Link Resolution via `1024teradl.com`**:
   - Automatically navigates to `1024teradl.com`, inputs the TeraBox shared link, and handles Cloudflare Turnstile challenges.
   - Intercepts backend `/api/proxy` responses over Chrome DevTools Protocol (CDP) to extract direct download links (`dlink`, `fast_download_url`, file names, sizes, and file IDs).
   - Intelligent DOM parser fallback with strict domain and file extension filtering.

2. **Concurrent & Resumable File Downloading**:
   - Download multiple files concurrently with a configurable concurrency limit (`--concurrency` / `-c`).
   - **Resumable Downloads**: Automatically resumes interrupted downloads using HTTP `Range: bytes=start-` headers and `.part` temporary files.
   - **Automatic Retries**: Uses exponential backoff (`--retry-delay` with `--max-retries`) to automatically recover from dropped connections, timeouts, or network interruptions.

3. **Dual Automation Engine Support**:
   - **`nodriver` (default)**: Pure-Python Chrome DevTools Protocol automation using system Google Chrome. Zero Node.js dependency, sandboxing-friendly, and automatically bypasses Cloudflare Turnstile detection.
   - **`playwright`**: Full Playwright + `playwright-stealth` engine support for standard Node/Playwright environments.

4. **Rich Terminal Experience**:
   - Interactive progress bars with real-time transfer speeds, bytes downloaded, ETAs, and retry status indicators.
   - Summary tables for discovered files and final download results.

---

## 📦 Installation

### Prerequisites
- **Python 3.10+**
- **Google Chrome** (recommended for `nodriver` default engine) or **Playwright Chromium** (`playwright install chromium`)

### Install from Source
1. Clone the repository and create a virtual environment:
   ```bash
   git clone https://github.com/moandor-y/terabox_downloader.git
   cd terabox_downloader
   python3 -m venv .venv
   source .venv/bin/activate
   ```
2. Install the package in editable mode:
   ```bash
   pip install -e .
   ```

---

## 🚀 Quick Start & Usage

### 1. Basic Download
Download all files in a TeraBox shared link with default settings (3 concurrent downloads, 10 automatic retries):
```bash
terabox-dl "https://terabox.com/s/1A2b3C4d5E6f7G8h9I0jKlM"
```

### 2. Specify Output Directory & Concurrency
Save downloaded files to `./my_videos` and download up to 5 files concurrently:
```bash
terabox-dl "https://terabox.com/s/1A2b3C4d5E6f7G8h9I0jKlM" -o ./my_videos -c 5
```

### 3. Dry-Run Mode (Extract Links Without Downloading)
Extract and preview all downloadable filenames, sizes, and direct URLs without writing any files to disk:
```bash
terabox-dl "https://terabox.com/s/1A2b3C4d5E6f7G8h9I0jKlM" --dry-run
```

### 4. Custom Retries & Automation Engine
Use 15 retries with a 3-second initial backoff delay, or explicitly select the Playwright engine:
```bash
terabox-dl "https://terabox.com/s/1A2b3C4d5E6f7G8h9I0jKlM" -r 15 --retry-delay 3.0 --engine playwright
```

---

## 🛠️ Command-Line Options

```
Usage: terabox-dl [OPTIONS] {url}

Arguments:
  * url          <str>   TeraBox shared URL (e.g., https://terabox.com/s/1... or https://1024tera.com/s/...)

Options:
  -o, --output-dir      <str>    Directory where downloaded files will be saved [default: ./downloads]
  -c, --concurrency     <int>    Maximum number of concurrent file downloads [default: 3]
  -r, --max-retries     <int>    Maximum number of automatic retries if a download is interrupted [default: 10]
  --retry-delay         <float>  Initial retry delay in seconds (uses exponential backoff) [default: 2.0]
  --headless / --no-headless     Run browser in headless mode (default --no-headless for faster Cloudflare bypass)
  -e, --engine          <str>    Browser automation engine ('auto', 'nodriver', or 'playwright') [default: auto]
  --chrome-path         <str>    Path to Google Chrome executable [default: /Applications/Google Chrome.app/Contents/MacOS/Google Chrome]
  --dry-run                      Extract and list download links without actually downloading the files
  -v, --verbose                  Enable verbose debug logging
  --help                         Show this message and exit.
```

---

## 🐍 Python API Example

You can also use `terabox-downloader` programmatically in your own asynchronous Python applications:

```python
import asyncio
from terabox_dl.models import DownloadConfig
from terabox_dl.automator import TeraBoxAutomator
from terabox_dl.downloader import AsyncDownloader

async def main():
    url = "https://terabox.com/s/1A2b3C4d5E6f7G8h9I0jKlM"
    config = DownloadConfig(
        output_dir="./downloads",
        concurrency=3,
        max_retries=10,
        browser_engine="auto",  # Uses nodriver by default on macOS
    )

    # 1. Extract file info and direct download links
    automator = TeraBoxAutomator(config)
    files = await automator.extract_files(url)
    print(f"Extracted {len(files)} file(s): {[f.filename for f in files]}")

    # 2. Concurrently download all files with range-resume and auto-retry
    downloader = AsyncDownloader(config)
    results = await downloader.download_all(files)
    for res in results:
        print(f"File: {res.file_info.filename} | Status: {res.status.value} | Downloaded: {res.bytes_downloaded} B")

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 🧪 Running the Test Suite

The project includes a comprehensive unit and integration test suite (using `pytest`, `respx`, and `pytest-asyncio`):

```bash
# Run all unit and integration tests
pytest -v
```

---

## 🏗️ Architecture

- `terabox_dl/cli.py`: Typer-based command-line interface, argument parsing, logging setup, and Rich tables/spinners.
- `terabox_dl/automator.py`: Browser automation (`TeraBoxAutomator`) using `nodriver` / `playwright` with CDP network domain interception (`Network.responseReceived`, `Network.loadingFinished`) and BeautifulSoup DOM fallback parsing.
- `terabox_dl/downloader.py`: Asynchronous concurrent downloader (`AsyncDownloader`) using `httpx.AsyncClient` with streaming response, HTTP `Range` headers, `.part` files, and exponential backoff.
- `terabox_dl/models.py`: Strongly-typed dataclasses (`FileInfo`, `DownloadResult`, `DownloadConfig`, and `DownloadStatus`).
- `terabox_dl/utils.py`: Filename sanitization, size string parsing, URL validation, and human-readable formatting.

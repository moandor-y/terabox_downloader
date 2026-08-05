"""Data models for TeraBox automated downloader."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional


class DownloadStatus(Enum):
    """Lifecycle state of a file download."""
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class FileInfo:
    """Metadata representing a single downloadable file or folder item."""
    filename: str
    download_url: str
    size_bytes: int = 0
    fs_id: Optional[str] = None
    isdir: bool = False
    headers: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not self.headers:
            self.headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/151.0.0.0 Safari/537.36"
                ),
                "Referer": "https://1024teradl.com/",
            }


@dataclass
class DownloadResult:
    """Outcome of attempting to download a FileInfo."""
    file_info: FileInfo
    status: DownloadStatus
    file_path: Optional[str] = None
    bytes_downloaded: int = 0
    attempts: int = 0
    error: Optional[str] = None


@dataclass
class DownloadConfig:
    """Configuration options for browser automation and downloading."""
    output_dir: str = "./downloads"
    concurrency: int = 3
    connections_per_file: int = 4
    max_retries: int = 10
    retry_delay: float = 2.0
    chunk_size: int = 1024 * 64  # 64 KB per read chunk
    browser_engine: str = "auto"  # "nodriver", "playwright", or "auto"
    headless: bool = False
    chrome_executable_path: Optional[str] = (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )

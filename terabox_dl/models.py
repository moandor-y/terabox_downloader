"""Data models for TeraBox automated downloader."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, Optional


class CloudflareBlockError(RuntimeError):
    """Raised when extraction is blocked by Cloudflare bot detection or Turnstile."""


class SandboxPolicyError(RuntimeError):
    """Raised when refusing to launch Chrome as root without --i-accept-no-sandbox."""


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


class ExtractedFileList(list):
    """A list of FileInfo objects carrying listing completeness metadata."""

    def __init__(
        self,
        iterable: Iterable[FileInfo] = (),
        *,
        truncated: bool = False,
        reason: Optional[str] = None,
        truncation_reason: Optional[str] = None,
    ):
        super().__init__(iterable)
        self.truncated: bool = bool(truncated)
        self._reason: Optional[str] = (
            reason if reason is not None else truncation_reason
        )

    @property
    def reason(self) -> Optional[str]:
        return self._reason

    @reason.setter
    def reason(self, value: Optional[str]) -> None:
        self._reason = value

    @property
    def truncation_reason(self) -> Optional[str]:
        return self._reason

    @truncation_reason.setter
    def truncation_reason(self, value: Optional[str]) -> None:
        self._reason = value


FileList = ExtractedFileList
FileListing = ExtractedFileList


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
    # Opt in to launching Chrome without its sandbox. Only has an effect when
    # running as root, where Chrome cannot enable the sandbox at all. Off by
    # default: see terabox_dl.automator.should_disable_sandbox.
    allow_no_sandbox: bool = False
    challenge_timeout_attempts: int = 30
    challenge_poll_interval: float = 1.0
    challenge_max_clicks: int = 3
    challenge_click_cooldown_attempts: int = 4

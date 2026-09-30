"""Unit tests for models.py."""

from terabox_dl.models import (
    CloudflareBlockError,
    DownloadConfig,
    DownloadResult,
    DownloadStatus,
    ExtractedFileList,
    FileInfo,
    FileList,
    FileListing,
    SandboxPolicyError,
)


def test_file_info_default_headers():
    fi = FileInfo(filename="test.mp4", download_url="https://example.com/download")
    assert fi.filename == "test.mp4"
    assert fi.download_url == "https://example.com/download"
    assert fi.size_bytes == 0
    assert "User-Agent" in fi.headers
    assert fi.headers["Referer"] == "https://1024teradl.com/"


def test_download_result():
    fi = FileInfo(filename="archive.zip", download_url="https://example.com/archive.zip", size_bytes=1024)
    res = DownloadResult(file_info=fi, status=DownloadStatus.PENDING)
    assert res.status == DownloadStatus.PENDING
    assert res.bytes_downloaded == 0
    assert res.file_path is None


def test_download_config_defaults():
    cfg = DownloadConfig()
    assert cfg.output_dir == "./downloads"
    assert cfg.concurrency == 3
    assert cfg.connections_per_file == 4
    assert cfg.max_retries == 10
    assert cfg.browser_engine == "auto"
    assert cfg.challenge_timeout_attempts == 30
    assert cfg.challenge_poll_interval == 1.0


def test_exception_hierarchy():
    assert issubclass(CloudflareBlockError, RuntimeError)
    assert issubclass(SandboxPolicyError, RuntimeError)


def test_extracted_file_list_and_aliases():
    assert FileList is ExtractedFileList
    assert FileListing is ExtractedFileList

    fi = FileInfo(filename="a.mp4", download_url="https://example.com/a.mp4")
    fl = ExtractedFileList([fi], truncated=True, reason="page 2 HTTP 429")
    assert isinstance(fl, list)
    assert fl == [fi]
    assert fl.truncated is True
    assert fl.reason == "page 2 HTTP 429"
    assert fl.truncation_reason == "page 2 HTTP 429"

    fl.truncation_reason = "updated reason"
    assert fl.reason == "updated reason"

    fl2 = FileList([fi], truncated=False, truncation_reason="init via truncation_reason")
    assert fl2.truncated is False
    assert fl2.reason == "init via truncation_reason"
    assert fl2.truncation_reason == "init via truncation_reason"

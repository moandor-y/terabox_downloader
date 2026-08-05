"""Unit tests for models.py."""

from terabox_dl.models import DownloadConfig, DownloadResult, DownloadStatus, FileInfo


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

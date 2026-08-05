"""Unit tests for downloader.py using pytest-asyncio and respx."""

import os
import pytest
import respx
import httpx

from terabox_dl.downloader import AsyncDownloader
from terabox_dl.models import DownloadConfig, DownloadStatus, FileInfo


@pytest.mark.asyncio
@respx.mock
async def test_download_file_success(tmp_path):
    config = DownloadConfig(output_dir=str(tmp_path), max_retries=2)
    downloader = AsyncDownloader(config)

    file_info = FileInfo(
        filename="test.txt",
        download_url="https://terabox.example.com/file1",
        size_bytes=14,
    )

    respx.get("https://terabox.example.com/file1").mock(
        return_value=httpx.Response(
            200,
            content=b"Hello TeraBox!",
            headers={"content-length": "14"},
        )
    )

    result = await downloader.download_file(file_info)
    assert result.status == DownloadStatus.COMPLETED
    assert result.attempts == 1
    assert result.bytes_downloaded == 14
    assert os.path.exists(result.file_path)
    with open(result.file_path, "rb") as f:
        assert f.read() == b"Hello TeraBox!"


@pytest.mark.asyncio
@respx.mock
async def test_download_file_retry_and_resume(tmp_path):
    config = DownloadConfig(output_dir=str(tmp_path), max_retries=3, retry_delay=0.01)
    downloader = AsyncDownloader(config)

    file_info = FileInfo(
        filename="resume.txt",
        download_url="https://terabox.example.com/resume",
        size_bytes=12,
    )

    # Pre-populate part file with 5 bytes
    part_path = os.path.join(str(tmp_path), "resume.txt.part")
    with open(part_path, "wb") as f:
        f.write(b"Hello")

    # Mock server response for Range: bytes=5-
    respx.get("https://terabox.example.com/resume").mock(
        return_value=httpx.Response(
            206,
            content=b" World!",
            headers={"content-length": "7", "content-range": "bytes 5-11/12"},
        )
    )

    result = await downloader.download_file(file_info)
    assert result.status == DownloadStatus.COMPLETED
    assert os.path.exists(result.file_path)
    assert not os.path.exists(part_path)
    with open(result.file_path, "rb") as f:
        assert f.read() == b"Hello World!"


@pytest.mark.asyncio
async def test_download_file_already_completed(tmp_path):
    config = DownloadConfig(output_dir=str(tmp_path))
    downloader = AsyncDownloader(config)

    # Create target file of size 12
    target_path = os.path.join(str(tmp_path), "finished.txt")
    with open(target_path, "wb") as f:
        f.write(b"Hello World!")

    file_info = FileInfo(
        filename="finished.txt",
        download_url="https://terabox.example.com/finished",
        size_bytes=12,
    )

    result = await downloader.download_file(file_info)
    assert result.status == DownloadStatus.COMPLETED
    assert result.attempts == 0
    assert result.bytes_downloaded == 12


@pytest.mark.asyncio
@respx.mock
async def test_download_all_concurrency(tmp_path):
    config = DownloadConfig(output_dir=str(tmp_path), concurrency=2)
    downloader = AsyncDownloader(config)

    files = [
        FileInfo(filename=f"file{i}.dat", download_url=f"https://example.com/file{i}", size_bytes=4)
        for i in range(3)
    ]

    for i in range(3):
        respx.get(f"https://example.com/file{i}").mock(
            return_value=httpx.Response(200, content=b"DATA")
        )

    results = await downloader.download_all(files)
    assert len(results) == 3
    for r in results:
        assert r.status == DownloadStatus.COMPLETED
        assert r.bytes_downloaded == 4

"""Integration test verifying end-to-end automator and downloader workflow."""

import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from terabox_dl.automator import TeraBoxAutomator
from terabox_dl.downloader import AsyncDownloader
from terabox_dl.models import DownloadConfig, DownloadStatus, FileInfo


@pytest.mark.asyncio
@respx.mock
@patch.object(TeraBoxAutomator, "extract_files")
async def test_end_to_end_extraction_and_download(mock_extract, tmp_path):
    config = DownloadConfig(
        output_dir=str(tmp_path),
        concurrency=2,
        max_retries=2,
    )

    sample_files = [
        FileInfo(
            filename="video1.mp4",
            download_url="https://d.terabox.example/video1.mp4",
            size_bytes=10,
        ),
        FileInfo(
            filename="document.pdf",
            download_url="https://d.terabox.example/document.pdf",
            size_bytes=15,
        ),
    ]
    mock_extract.return_value = sample_files

    respx.get("https://d.terabox.example/video1.mp4").mock(
        return_value=httpx.Response(200, content=b"VIDEO_DATA", headers={"content-length": "10"})
    )
    respx.get("https://d.terabox.example/document.pdf").mock(
        return_value=httpx.Response(200, content=b"PDF_FILE_CONTENT", headers={"content-length": "15"})
    )

    automator = TeraBoxAutomator(config)
    files = await automator.extract_files("https://terabox.com/s/1A2b3C4d5E6f7G8h9I0jKlM")
    assert len(files) == 2

    downloader = AsyncDownloader(config)
    results = await downloader.download_all(files)

    assert len(results) == 2
    for r in results:
        assert r.status == DownloadStatus.COMPLETED
        assert os.path.exists(r.file_path)
        assert not os.path.exists(r.file_path + ".part")

    with open(os.path.join(str(tmp_path), "video1.mp4"), "rb") as f:
        assert f.read() == b"VIDEO_DATA"
    with open(os.path.join(str(tmp_path), "document.pdf"), "rb") as f:
        assert f.read() == b"PDF_FILE_CONTENT"

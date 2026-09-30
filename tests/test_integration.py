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


@pytest.mark.asyncio
@respx.mock
@patch.object(TeraBoxAutomator, "extract_files")
async def test_end_to_end_retry_truncated_listing_and_partial_download_recovery(
    mock_extract, tmp_path
):
    from terabox_dl.cli import run_downloader
    from terabox_dl.models import ExtractedFileList

    f1_v1 = FileInfo(
        filename="file1.bin",
        download_url="https://d.terabox.example/v1/file1.bin",
        size_bytes=10,
        fs_id="101",
    )
    f2_v1 = FileInfo(
        filename="file2.bin",
        download_url="https://d.terabox.example/v1/file2.bin",
        size_bytes=12,
        fs_id="102",
    )
    f1_v2 = FileInfo(
        filename="file1.bin",
        download_url="https://d.terabox.example/v2/file1.bin",
        size_bytes=10,
        fs_id="101",
    )
    f2_v2 = FileInfo(
        filename="file2.bin",
        download_url="https://d.terabox.example/v2/file2.bin",
        size_bytes=12,
        fs_id="102",
    )
    f3_v2 = FileInfo(
        filename="file3.bin",
        download_url="https://d.terabox.example/v2/file3.bin",
        size_bytes=8,
        fs_id="103",
    )

    mock_extract.side_effect = [
        RuntimeError("Transient extraction error"),
        ExtractedFileList([f1_v1, f2_v1], truncated=True, reason="page 2 failed"),
        ExtractedFileList([f1_v2, f2_v2, f3_v2], truncated=False),
    ]

    # Cycle 2 routes: file1 completes, file2 transfers only 5 of 12 bytes and fails
    route_f1_v1 = respx.get("https://d.terabox.example/v1/file1.bin").mock(
        return_value=httpx.Response(200, content=b"0123456789", headers={"content-length": "10"})
    )
    respx.get("https://d.terabox.example/v1/file2.bin").mock(
        return_value=httpx.Response(200, content=b"HELLO", headers={"content-length": "12"})
    )

    # Cycle 3 routes: file1_v2 must NOT be requested; file2_v2 resumes from byte 5; file3_v2 completes
    route_f1_v2 = respx.get("https://d.terabox.example/v2/file1.bin").mock(
        return_value=httpx.Response(500)
    )
    route_f2_v2 = respx.get("https://d.terabox.example/v2/file2.bin").mock(
        return_value=httpx.Response(
            206,
            content=b"_WORLD!",
            headers={"content-length": "7", "content-range": "bytes 5-11/12"},
        )
    )
    route_f3_v2 = respx.get("https://d.terabox.example/v2/file3.bin").mock(
        return_value=httpx.Response(200, content=b"FILE_3!!", headers={"content-length": "8"})
    )

    exit_code = await run_downloader(
        url="https://terabox.com/s/1A2b3C4d5E6f7G8h9I0jKlM",
        output_dir=str(tmp_path),
        concurrency=2,
        max_retries=1,
        retry_delay=0.0,
        headless=True,
        engine="auto",
        chrome_path=None,
        dry_run=False,
        ignore=[],
        allow_no_sandbox=False,
    )

    assert exit_code == 0
    assert mock_extract.call_count == 3
    assert route_f1_v1.called
    assert not route_f1_v2.called
    assert route_f2_v2.called
    assert route_f2_v2.calls.last.request.headers.get("Range") == "bytes=5-"
    assert route_f3_v2.called

    with open(os.path.join(str(tmp_path), "file1.bin"), "rb") as f:
        assert f.read() == b"0123456789"
    with open(os.path.join(str(tmp_path), "file2.bin"), "rb") as f:
        assert f.read() == b"HELLO_WORLD!"
    with open(os.path.join(str(tmp_path), "file3.bin"), "rb") as f:
        assert f.read() == b"FILE_3!!"

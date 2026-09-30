"""Unit tests for cli.py using Typer CliRunner and unittest.mock."""

from unittest.mock import AsyncMock, patch
from typer.testing import CliRunner

from terabox_dl.automator import ROOT_SANDBOX_ERROR
from terabox_dl.cli import app
from terabox_dl.models import (
    CloudflareBlockError,
    DownloadResult,
    DownloadStatus,
    ExtractedFileList,
    FileInfo,
    SandboxPolicyError,
)

runner = CliRunner()


def test_cli_invalid_url():
    result = runner.invoke(app, ["https://google.com/invalid_link"])
    assert result.exit_code == 1
    assert "not a valid TeraBox share link" in result.stdout


@patch("terabox_dl.cli.TeraBoxAutomator")
def test_cli_dry_run_success(mock_automator_cls):
    mock_automator = mock_automator_cls.return_value
    mock_automator.extract_files = AsyncMock(
        return_value=[
            FileInfo(
                filename="demo.mp4",
                download_url="https://d.1024teradl.com/dl/demo.mp4",
                size_bytes=1048576,
            )
        ]
    )

    result = runner.invoke(app, ["https://terabox.com/s/1A2b3C4d5E6f7G8h9I0jKlM", "--dry-run"])
    assert result.exit_code == 0
    assert "demo.mp4" in result.stdout
    assert "--dry-run enabled" in result.stdout


@patch("terabox_dl.cli.AsyncDownloader")
@patch("terabox_dl.cli.TeraBoxAutomator")
def test_cli_download_success(mock_automator_cls, mock_downloader_cls):
    mock_automator = mock_automator_cls.return_value
    mock_file = FileInfo(
        filename="video.mp4",
        download_url="https://d.1024teradl.com/dl/video.mp4",
        size_bytes=2097152,
    )
    mock_automator.extract_files = AsyncMock(return_value=[mock_file])

    mock_downloader = mock_downloader_cls.return_value
    mock_downloader.download_all = AsyncMock(
        return_value=[
            DownloadResult(
                file_info=mock_file,
                status=DownloadStatus.COMPLETED,
                file_path="./downloads/video.mp4",
                bytes_downloaded=2097152,
                attempts=1,
            )
        ]
    )

    result = runner.invoke(
        app,
        ["https://terabox.com/s/1A2b3C4d5E6f7G8h9I0jKlM", "-c", "5", "-o", "./my_dl"],
    )
    assert result.exit_code == 0
    assert "Found 1 downloadable file" in result.stdout
    assert "COMPLETED" in result.stdout
    assert "All 1 file(s) successfully downloaded" in result.stdout


@patch("terabox_dl.cli.TeraBoxAutomator")
def test_cli_ignore_files(mock_automator_cls):
    mock_automator = mock_automator_cls.return_value
    mock_automator.extract_files = AsyncMock(
        return_value=[
            FileInfo(
                filename="demo.mp4",
                download_url="https://d.1024teradl.com/dl/demo.mp4",
                size_bytes=1048576,
            ),
            FileInfo(
                filename="ignore_me.txt",
                download_url="https://d.1024teradl.com/dl/ignore_me.txt",
                size_bytes=1024,
            )
        ]
    )

    result = runner.invoke(app, ["https://terabox.com/s/1A2b3C4d5E6f7G8h9I0jKlM", "--dry-run", "-i", "ignore_me.txt"])
    assert result.exit_code == 0
    assert "demo.mp4" in result.stdout
    assert "ignore_me.txt" not in result.stdout
    assert "Ignored 1 file(s) based on --ignore list." in result.stdout


@patch("terabox_dl.cli.TeraBoxAutomator")
def test_cli_ignore_all_files(mock_automator_cls):
    mock_automator = mock_automator_cls.return_value
    mock_automator.extract_files = AsyncMock(
        return_value=[
            FileInfo(
                filename="ignore_me.txt",
                download_url="https://d.1024teradl.com/dl/ignore_me.txt",
                size_bytes=1024,
            )
        ]
    )

    result = runner.invoke(app, ["https://terabox.com/s/1A2b3C4d5E6f7G8h9I0jKlM", "--dry-run", "-i", "ignore_me.txt"])
    assert result.exit_code == 1
    assert "All downloadable files were ignored." in result.stdout
    assert mock_automator.extract_files.call_count == 1


@patch("terabox_dl.cli.AsyncDownloader")
@patch("terabox_dl.cli.TeraBoxAutomator")
def test_cli_retries_on_extraction_error_and_empty_list_then_succeeds(
    mock_automator_cls, mock_downloader_cls
):
    mock_file = FileInfo(
        filename="recovered.mp4",
        download_url="https://d.1024teradl.com/dl/recovered.mp4",
        size_bytes=1024,
        fs_id="100",
    )
    mock_automator = mock_automator_cls.return_value
    mock_automator.extract_files = AsyncMock(
        side_effect=[
            RuntimeError("TeraBox API Error (500): transient gateway error"),
            [],
            ExtractedFileList([mock_file], truncated=False),
        ]
    )

    mock_downloader = mock_downloader_cls.return_value
    mock_downloader.download_all = AsyncMock(
        return_value=[
            DownloadResult(
                file_info=mock_file,
                status=DownloadStatus.COMPLETED,
                file_path="./downloads/recovered.mp4",
                bytes_downloaded=1024,
                attempts=1,
            )
        ]
    )

    result = runner.invoke(
        app,
        ["https://terabox.com/s/1A2b3C4d5E6f7G8h9I0jKlM", "--retry-delay", "0"],
    )
    assert result.exit_code == 0
    assert mock_automator.extract_files.call_count == 3
    assert mock_downloader.download_all.call_count == 1
    assert "All 1 file(s) successfully downloaded" in result.stdout


@patch("terabox_dl.cli.AsyncDownloader")
@patch("terabox_dl.cli.TeraBoxAutomator")
def test_cli_truncated_listing_downloads_partial_and_retries_for_remaining(
    mock_automator_cls, mock_downloader_cls
):
    f1 = FileInfo(
        filename="part1.zip",
        download_url="https://d.1024teradl.com/dl/part1_v1.zip",
        size_bytes=1000,
        fs_id="1",
    )
    f2 = FileInfo(
        filename="part2.zip",
        download_url="https://d.1024teradl.com/dl/part2_v1.zip",
        size_bytes=2000,
        fs_id="2",
    )
    f1_v2 = FileInfo(
        filename="part1.zip",
        download_url="https://d.1024teradl.com/dl/part1_v2.zip",
        size_bytes=1000,
        fs_id="1",
    )
    f2_v2 = FileInfo(
        filename="part2.zip",
        download_url="https://d.1024teradl.com/dl/part2_v2.zip",
        size_bytes=2000,
        fs_id="2",
    )
    f3 = FileInfo(
        filename="part3.zip",
        download_url="https://d.1024teradl.com/dl/part3_v2.zip",
        size_bytes=3000,
        fs_id="3",
    )

    mock_automator = mock_automator_cls.return_value
    mock_automator.extract_files = AsyncMock(
        side_effect=[
            ExtractedFileList([f1, f2], truncated=True, reason="page 2 HTTP 429"),
            ExtractedFileList([f1_v2, f2_v2, f3], truncated=False),
        ]
    )

    mock_downloader = mock_downloader_cls.return_value
    mock_downloader.download_all = AsyncMock(
        side_effect=[
            [
                DownloadResult(
                    file_info=f1,
                    status=DownloadStatus.COMPLETED,
                    file_path="./downloads/part1.zip",
                    bytes_downloaded=1000,
                    attempts=1,
                ),
                DownloadResult(
                    file_info=f2,
                    status=DownloadStatus.COMPLETED,
                    file_path="./downloads/part2.zip",
                    bytes_downloaded=2000,
                    attempts=1,
                ),
            ],
            [
                DownloadResult(
                    file_info=f3,
                    status=DownloadStatus.COMPLETED,
                    file_path="./downloads/part3.zip",
                    bytes_downloaded=3000,
                    attempts=1,
                ),
            ],
        ]
    )

    result = runner.invoke(
        app,
        ["https://terabox.com/s/1A2b3C4d5E6f7G8h9I0jKlM", "--retry-delay", "0"],
    )
    assert result.exit_code == 0
    assert mock_automator.extract_files.call_count == 2
    assert mock_downloader.download_all.call_count == 2
    first_batch = mock_downloader.download_all.call_args_list[0][0][0]
    second_batch = mock_downloader.download_all.call_args_list[1][0][0]
    assert [f.filename for f in first_batch] == ["part1.zip", "part2.zip"]
    # Cycle 2 only downloads the newly discovered part3.zip without re-downloading part1 or part2
    assert [f.filename for f in second_batch] == ["part3.zip"]
    assert "All 3 file(s) successfully downloaded" in result.stdout


@patch("terabox_dl.cli.AsyncDownloader")
@patch("terabox_dl.cli.TeraBoxAutomator")
def test_cli_failed_download_reextracts_and_retries_only_incomplete_files(
    mock_automator_cls, mock_downloader_cls
):
    f1_v1 = FileInfo(
        filename="ok.mp4",
        download_url="https://d.1024teradl.com/dl/ok_v1.mp4",
        size_bytes=500,
        fs_id="10",
    )
    f2_v1 = FileInfo(
        filename="flaky.mp4",
        download_url="https://d.1024teradl.com/dl/flaky_expired.mp4",
        size_bytes=1500,
        fs_id="20",
    )
    f1_v2 = FileInfo(
        filename="ok.mp4",
        download_url="https://d.1024teradl.com/dl/ok_v2.mp4",
        size_bytes=500,
        fs_id="10",
    )
    f2_v2 = FileInfo(
        filename="flaky.mp4",
        download_url="https://d.1024teradl.com/dl/flaky_fresh.mp4",
        size_bytes=1500,
        fs_id="20",
    )

    mock_automator = mock_automator_cls.return_value
    mock_automator.extract_files = AsyncMock(
        side_effect=[
            ExtractedFileList([f1_v1, f2_v1], truncated=False),
            ExtractedFileList([f1_v2, f2_v2], truncated=False),
        ]
    )

    mock_downloader = mock_downloader_cls.return_value
    mock_downloader.download_all = AsyncMock(
        side_effect=[
            [
                DownloadResult(
                    file_info=f1_v1,
                    status=DownloadStatus.COMPLETED,
                    file_path="./downloads/ok.mp4",
                    bytes_downloaded=500,
                    attempts=1,
                ),
                DownloadResult(
                    file_info=f2_v1,
                    status=DownloadStatus.FAILED,
                    bytes_downloaded=200,
                    attempts=3,
                    error="HTTP 403 Forbidden (expired link)",
                ),
            ],
            [
                DownloadResult(
                    file_info=f2_v2,
                    status=DownloadStatus.COMPLETED,
                    file_path="./downloads/flaky.mp4",
                    bytes_downloaded=1500,
                    attempts=1,
                ),
            ],
        ]
    )

    result = runner.invoke(
        app,
        ["https://terabox.com/s/1A2b3C4d5E6f7G8h9I0jKlM", "--retry-delay", "0"],
    )
    assert result.exit_code == 0
    assert mock_automator.extract_files.call_count == 2
    assert mock_downloader.download_all.call_count == 2
    retry_batch = mock_downloader.download_all.call_args_list[1][0][0]
    assert len(retry_batch) == 1
    assert retry_batch[0].filename == "flaky.mp4"
    assert retry_batch[0].download_url == "https://d.1024teradl.com/dl/flaky_fresh.mp4"
    assert "All 2 file(s) successfully downloaded" in result.stdout


@patch("terabox_dl.cli.AsyncDownloader")
@patch("terabox_dl.cli.TeraBoxAutomator")
def test_cli_dry_run_retries_on_error_and_truncated_listing(
    mock_automator_cls, mock_downloader_cls
):
    f1 = FileInfo(filename="doc1.pdf", download_url="https://d/doc1.pdf", size_bytes=100, fs_id="1")
    f2 = FileInfo(filename="doc2.pdf", download_url="https://d/doc2.pdf", size_bytes=200, fs_id="2")

    mock_automator = mock_automator_cls.return_value
    mock_automator.extract_files = AsyncMock(
        side_effect=[
            RuntimeError("Temporary extraction failure"),
            ExtractedFileList([f1], truncated=True, reason="page 2 timeout"),
            ExtractedFileList([f1, f2], truncated=False),
        ]
    )

    result = runner.invoke(
        app,
        ["https://terabox.com/s/1A2b3C4d5E6f7G8h9I0jKlM", "--dry-run", "--retry-delay", "0"],
    )
    assert result.exit_code == 0
    assert mock_automator.extract_files.call_count == 3
    mock_downloader_cls.assert_not_called()
    assert "doc1.pdf" in result.stdout
    assert "doc2.pdf" in result.stdout
    assert "--dry-run enabled" in result.stdout


@patch("terabox_dl.cli.AsyncDownloader")
@patch("terabox_dl.cli.TeraBoxAutomator")
def test_cli_cloudflare_block_fast_fails_without_retry(
    mock_automator_cls, mock_downloader_cls
):
    mock_automator = mock_automator_cls.return_value
    mock_automator.extract_files = AsyncMock(
        side_effect=CloudflareBlockError("Blocked by Cloudflare Turnstile challenge")
    )

    result = runner.invoke(
        app,
        ["https://terabox.com/s/1A2b3C4d5E6f7G8h9I0jKlM", "--retry-delay", "0"],
    )
    assert result.exit_code == 1
    assert mock_automator.extract_files.call_count == 1
    mock_downloader_cls.assert_not_called()
    assert "Cloudflare" in result.stdout


@patch("terabox_dl.cli.AsyncDownloader")
@patch("terabox_dl.cli.TeraBoxAutomator")
def test_cli_root_sandbox_refusal_fast_fails_without_retry(
    mock_automator_cls, mock_downloader_cls
):
    mock_automator = mock_automator_cls.return_value
    mock_automator.extract_files = AsyncMock(
        side_effect=SandboxPolicyError(ROOT_SANDBOX_ERROR)
    )

    result = runner.invoke(
        app,
        ["https://terabox.com/s/1A2b3C4d5E6f7G8h9I0jKlM", "--retry-delay", "0"],
    )
    assert result.exit_code == 1
    assert mock_automator.extract_files.call_count == 1
    mock_downloader_cls.assert_not_called()
    assert "Refusing to launch Chrome as root" in result.stdout


@patch("terabox_dl.cli.AsyncDownloader")
@patch("terabox_dl.cli.TeraBoxAutomator")
def test_cli_ignore_all_files_non_dry_run_fast_fails_without_retry(
    mock_automator_cls, mock_downloader_cls
):
    mock_automator = mock_automator_cls.return_value
    mock_automator.extract_files = AsyncMock(
        return_value=ExtractedFileList(
            [
                FileInfo(
                    filename="skip1.txt",
                    download_url="https://d/skip1.txt",
                    size_bytes=100,
                ),
                FileInfo(
                    filename="skip2.txt",
                    download_url="https://d/skip2.txt",
                    size_bytes=200,
                ),
            ],
            truncated=False,
        )
    )

    result = runner.invoke(
        app,
        [
            "https://terabox.com/s/1A2b3C4d5E6f7G8h9I0jKlM",
            "-i",
            "skip1.txt",
            "-i",
            "skip2.txt",
            "--retry-delay",
            "0",
        ],
    )
    assert result.exit_code == 1
    assert mock_automator.extract_files.call_count == 1
    mock_downloader_cls.assert_not_called()
    assert "All downloadable files were ignored." in result.stdout


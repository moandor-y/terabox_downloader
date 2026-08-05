"""Unit tests for cli.py using Typer CliRunner and unittest.mock."""

from unittest.mock import AsyncMock, patch
from typer.testing import CliRunner

from terabox_dl.cli import app
from terabox_dl.models import DownloadResult, DownloadStatus, FileInfo

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

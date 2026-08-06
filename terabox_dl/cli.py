"""Command-line interface for terabox-downloader using Typer and Rich."""

import asyncio
import logging
import os
import sys
from typing import List, Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from terabox_dl import __version__
from terabox_dl.automator import TeraBoxAutomator
from terabox_dl.downloader import AsyncDownloader
from terabox_dl.models import DownloadConfig, DownloadResult, DownloadStatus, FileInfo
from terabox_dl.utils import format_bytes, is_valid_terabox_url

app = typer.Typer(
    name="terabox-dl",
    help="Automated concurrent resumable file downloader for TeraBox links using 1024teradl.com and browser automation.",
    add_completion=False,
)
console = Console()


def setup_logging(verbose: bool):
    """Configure structured logging with RichHandler."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=verbose)],
    )
    
    # Silence third-party noise that corrupts the progress bar
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("nodriver").setLevel(logging.WARNING)


def print_files_table(files: List[FileInfo]):
    """Print a summary table of extracted files."""
    table = Table(title=f"Extracted TeraBox Files ({len(files)} total)", show_lines=True)
    table.add_column("Index", style="dim", width=6)
    table.add_column("Filename", style="cyan")
    table.add_column("Size", style="green", justify="right")
    table.add_column("Direct Link", style="blue", overflow="fold")

    for idx, f in enumerate(files, 1):
        table.add_row(
            str(idx),
            f.filename,
            format_bytes(f.size_bytes) if f.size_bytes > 0 else "Unknown",
            f.download_url,
        )
    console.print(table)


def print_results_table(results: List[DownloadResult]):
    """Print a summary table of download results."""
    table = Table(title="Download Results Summary", show_lines=True)
    table.add_column("Filename", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("Size Downloaded", style="green", justify="right")
    table.add_column("Attempts", justify="right", width=8)
    table.add_column("Saved Path / Error", overflow="fold")

    for r in results:
        status_style = "bold green" if r.status == DownloadStatus.COMPLETED else "bold red"
        status_text = f"[{status_style}]{r.status.value.upper()}[/{status_style}]"
        info = r.file_path if r.status == DownloadStatus.COMPLETED else str(r.error)
        table.add_row(
            r.file_info.filename,
            status_text,
            format_bytes(r.bytes_downloaded),
            str(r.attempts),
            info or "",
        )
    console.print(table)


async def run_downloader(
    url: str,
    output_dir: str,
    concurrency: int,
    max_retries: int,
    retry_delay: float,
    headless: bool,
    engine: str,
    chrome_path: Optional[str],
    dry_run: bool,
) -> int:
    """Core async workflow for automating extraction and downloading."""
    if not is_valid_terabox_url(url):
        console.print(f"[bold red]Error:[/bold red] '{url}' is not a valid TeraBox share link.")
        return 1

    config = DownloadConfig(
        output_dir=output_dir,
        concurrency=concurrency,
        max_retries=max_retries,
        retry_delay=retry_delay,
        browser_engine=engine,
        headless=headless,
        chrome_executable_path=chrome_path,
    )

    console.print(
        f"\n[bold magenta]🚀 terabox-dl v{__version__}[/bold magenta] — Automated TeraBox Downloader"
    )
    console.print(f"🔗 [blue]Target URL:[/blue] {url}")
    console.print(
        f"⚙️  [dim]Engine: {config.browser_engine} | Headless: {config.headless} | Concurrency: {config.concurrency} | Output: {config.output_dir}[/dim]\n"
    )

    automator = TeraBoxAutomator(config)
    console.print("[cyan]🔍 Step 1: Extracting download links via 1024teradl.com...[/cyan]")

    try:
        files = await automator.extract_files(url)
    except Exception as exc:
        console.print(f"[bold red]✗ Extraction failed:[/bold red] {exc}")
        return 1

    if not files:
        console.print(
            "[bold yellow]⚠️  No downloadable files found for this TeraBox link.[/bold yellow]"
        )
        return 1

    console.print(f"[bold green]✓ Found {len(files)} downloadable file(s)![/bold green]\n")
    print_files_table(files)

    if dry_run:
        console.print("\n[yellow](--dry-run enabled: skipping file download)[/yellow]")
        return 0

    console.print(
        f"\n[cyan]📥 Step 2: Downloading files (concurrency={config.concurrency}, max_retries={config.max_retries})...[/cyan]"
    )
    downloader = AsyncDownloader(config)
    results = await downloader.download_all(files)

    console.print("\n[bold]Download summary:[/bold]")
    print_results_table(results)

    success_count = sum(1 for r in results if r.status == DownloadStatus.COMPLETED)
    fail_count = len(results) - success_count

    if fail_count == 0:
        console.print(
            f"\n[bold green]🎉 All {success_count} file(s) successfully downloaded to '{config.output_dir}'![/bold green]"
        )
        return 0
    else:
        console.print(
            f"\n[bold red]⚠️  {fail_count} file(s) failed to download. Please check the error logs above.[/bold red]"
        )
        return 1


@app.command()
def main(
    url: str = typer.Argument(
        ...,
        help="TeraBox shared URL (e.g., https://terabox.com/s/1... or https://1024tera.com/s/...)",
    ),
    output_dir: str = typer.Option(
        "./downloads",
        "--output-dir",
        "-o",
        help="Directory where downloaded files will be saved",
    ),
    concurrency: int = typer.Option(
        3,
        "--concurrency",
        "-c",
        help="Maximum number of concurrent file downloads",
    ),
    max_retries: int = typer.Option(
        10,
        "--max-retries",
        "-r",
        help="Maximum number of automatic retries if a download is interrupted",
    ),
    retry_delay: float = typer.Option(
        2.0,
        "--retry-delay",
        help="Initial retry delay in seconds (uses exponential backoff)",
    ),
    headless: bool = typer.Option(
        False,
        "--headless/--no-headless",
        help="Run browser in headless mode (default --no-headless for faster Cloudflare challenge resolution)",
    ),
    engine: str = typer.Option(
        "auto",
        "--engine",
        "-e",
        help="Browser automation engine ('auto', 'nodriver', or 'playwright')",
    ),
    chrome_path: Optional[str] = typer.Option(
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "--chrome-path",
        help="Path to Google Chrome executable (used by nodriver engine)",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Extract and list download links without actually downloading the files",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose debug logging",
    ),
):
    """Automated concurrent resumable file downloader for TeraBox links using 1024teradl.com and browser automation."""
    setup_logging(verbose)
    exit_code = asyncio.run(
        run_downloader(
            url=url,
            output_dir=output_dir,
            concurrency=concurrency,
            max_retries=max_retries,
            retry_delay=retry_delay,
            headless=headless,
            engine=engine,
            chrome_path=chrome_path,
            dry_run=dry_run,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    app()

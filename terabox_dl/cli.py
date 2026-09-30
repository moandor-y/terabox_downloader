"""Command-line interface for terabox-downloader using Typer and Rich."""

import asyncio
import logging
import os
import sys
from typing import Dict, List, Optional, Set

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from terabox_dl import __version__
from terabox_dl.automator import (
    ROOT_SANDBOX_ERROR,
    TeraBoxAutomator,
    is_cloudflare_error,
)
from terabox_dl.downloader import AsyncDownloader
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
from terabox_dl.utils import format_bytes, is_valid_terabox_url, sanitize_filename

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


def _file_identity(file_info: FileInfo) -> str:
    """Return a stable cross-cycle identity key for a FileInfo item."""
    if file_info.fs_id:
        return f"fs:{file_info.fs_id}"
    return f"name:{sanitize_filename(file_info.filename)}"


def _is_unrecoverable_error(exc: BaseException) -> bool:
    """Return True if an extraction error is unrecoverable and must abort immediately."""
    if isinstance(exc, (ValueError, SandboxPolicyError, CloudflareBlockError)):
        return True
    if is_cloudflare_error(exc):
        return True
    msg = str(exc).lower()
    if (
        ROOT_SANDBOX_ERROR.lower() in msg
        or "refusing to launch chrome as root" in msg
    ):
        return True
    return False


def _is_listing_truncated(files: List[FileInfo], automator: object) -> bool:
    """Check whether the extracted file listing was flagged as truncated/incomplete."""
    return (
        getattr(files, "truncated", False) is True
        or getattr(automator, "last_listing_truncated", False) is True
        or any(getattr(f, "truncated", False) is True for f in (files or []))
    )


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
    ignore: List[str],
    allow_no_sandbox: bool = False,
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
        allow_no_sandbox=allow_no_sandbox,
    )

    console.print(
        f"\n[bold magenta]🚀 terabox-dl v{__version__}[/bold magenta] — Automated TeraBox Downloader"
    )
    console.print(f"🔗 [blue]Target URL:[/blue] {url}")
    console.print(
        f"⚙️  [dim]Engine: {config.browser_engine} | Headless: {config.headless} | Concurrency: {config.concurrency} | Output: {config.output_dir}[/dim]\n"
    )

    automator = TeraBoxAutomator(config)
    downloader: Optional[AsyncDownloader] = None
    discovered_files: Dict[str, FileInfo] = {}
    completed_results: Dict[str, DownloadResult] = {}
    completed_filenames: Set[str] = set()
    cycle = 0

    def _is_completed(f: FileInfo) -> bool:
        key = _file_identity(f)
        if key in completed_results:
            return True
        name_key = f"name:{sanitize_filename(f.filename)}"
        if name_key in completed_results:
            return True
        if not f.fs_id and sanitize_filename(f.filename) in completed_filenames:
            return True
        return False

    while True:
        cycle += 1
        if cycle == 1:
            console.print("[cyan]🔍 Step 1: Extracting download links via 1024teradl.com...[/cyan]")
        else:
            console.print(
                f"\n[bold yellow]🔄 Retry cycle {cycle}: Re-extracting download links via 1024teradl.com...[/bold yellow]"
            )

        if isinstance(getattr(automator, "last_listing_truncated", None), bool):
            automator.last_listing_truncated = False

        try:
            files = await automator.extract_files(url)
        except (StopIteration, StopAsyncIteration):
            raise
        except Exception as exc:
            if _is_unrecoverable_error(exc):
                console.print(f"[bold red]✗ Extraction failed:[/bold red] {exc}")
                return 1
            console.print(
                f"[bold yellow]⚠️  Extraction failed (cycle {cycle}): {exc}. Retrying...[/bold yellow]"
            )
            if config.retry_delay > 0:
                await asyncio.sleep(config.retry_delay)
            continue

        is_truncated = _is_listing_truncated(files, automator)

        if not files:
            console.print(
                "[bold yellow]⚠️  No downloadable files found for this TeraBox link. Retrying...[/bold yellow]"
            )
            if config.retry_delay > 0:
                await asyncio.sleep(config.retry_delay)
            continue

        if ignore:
            filtered_files = [f for f in files if f.filename not in ignore]
            ignored_count = len(files) - len(filtered_files)
            if ignored_count > 0:
                console.print(
                    f"[bold yellow]ℹ️  Ignored {ignored_count} file(s) based on --ignore list.[/bold yellow]"
                )
            files = filtered_files

            if not files:
                if not is_truncated and not discovered_files:
                    console.print(
                        "[bold yellow]⚠️  All downloadable files were ignored.[/bold yellow]"
                    )
                    return 1
                if config.retry_delay > 0:
                    await asyncio.sleep(config.retry_delay)
                continue

        for f in files:
            safe_name = sanitize_filename(f.filename)
            name_key = f"name:{safe_name}"
            key = _file_identity(f)
            if f.fs_id and name_key in discovered_files and key not in discovered_files:
                discovered_files.pop(name_key, None)
                if name_key in completed_results and key not in completed_results:
                    completed_results[key] = completed_results.pop(name_key)
            elif not f.fs_id:
                for existing_key, existing_file in list(discovered_files.items()):
                    if sanitize_filename(existing_file.filename) == safe_name:
                        key = existing_key
                        break
            discovered_files[key] = f

        if dry_run:
            if is_truncated:
                console.print(
                    f"[bold yellow]⚠️  Found {len(files)} file(s), but listing is incomplete/truncated. Retrying extraction...[/bold yellow]"
                )
                if config.retry_delay > 0:
                    await asyncio.sleep(config.retry_delay)
                continue
            all_discovered = list(discovered_files.values())
            console.print(
                f"[bold green]✓ Found {len(all_discovered)} downloadable file(s)![/bold green]\n"
            )
            print_files_table(all_discovered)
            console.print("\n[yellow](--dry-run enabled: skipping file download)[/yellow]")
            return 0

        if is_truncated:
            pending_map: Dict[str, FileInfo] = {}
            for f in files:
                if not _is_completed(f):
                    key = _file_identity(f)
                    pending_map[key] = discovered_files.get(key, f)
            pending_files = list(pending_map.values())
        else:
            pending_files = [
                f for f in discovered_files.values() if not _is_completed(f)
            ]

        console.print(f"[bold green]✓ Found {len(files)} downloadable file(s)![/bold green]\n")
        print_files_table(files)

        cycle_failed = False
        if pending_files:
            console.print(
                f"\n[cyan]📥 Step 2: Downloading files (concurrency={config.concurrency}, max_retries={config.max_retries})...[/cyan]"
            )
            if downloader is None:
                downloader = AsyncDownloader(config)
            cycle_results = await downloader.download_all(pending_files)
            for r in cycle_results:
                key = _file_identity(r.file_info)
                if r.status == DownloadStatus.COMPLETED:
                    completed_results[key] = r
                    completed_filenames.add(sanitize_filename(r.file_info.filename))
                else:
                    cycle_failed = True

        all_discovered_completed = bool(discovered_files) and all(
            _is_completed(f) for f in discovered_files.values()
        )

        if not is_truncated and not cycle_failed and all_discovered_completed:
            final_results: List[DownloadResult] = []
            for key, f in discovered_files.items():
                res = completed_results.get(key) or completed_results.get(
                    f"name:{sanitize_filename(f.filename)}"
                )
                if res is not None:
                    final_results.append(res)
            console.print("\n[bold]Download summary:[/bold]")
            print_results_table(final_results)
            console.print(
                f"\n[bold green]🎉 All {len(final_results)} file(s) successfully downloaded to '{config.output_dir}'![/bold green]"
            )
            return 0

        if cycle_failed:
            console.print(
                "\n[bold yellow]⚠️  Some file(s) failed to download. Re-running extraction to obtain fresh links...[/bold yellow]"
            )
        elif is_truncated:
            console.print(
                "\n[bold yellow]⚠️  File listing was truncated. Re-running extraction to fetch remaining files...[/bold yellow]"
            )

        if config.retry_delay > 0:
            await asyncio.sleep(config.retry_delay)


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
    ignore: List[str] = typer.Option(
        None,
        "--ignore",
        "-i",
        help="Filename to ignore during download (use multiple times to ignore multiple files, e.g., -i file1.txt -i file2.txt)",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose debug logging",
    ),
    allow_no_sandbox: bool = typer.Option(
        False,
        "--i-accept-no-sandbox",
        help=(
            "Allow running as root with the Chrome sandbox DISABLED. Unsafe: a "
            "malicious ad on the extraction page could then execute as root. "
            "Only use in a disposable container."
        ),
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
            ignore=ignore,
            allow_no_sandbox=allow_no_sandbox,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    app()

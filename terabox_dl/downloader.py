"""Concurrent, resumable asynchronous file downloader with automatic retry."""

import asyncio
import os
from typing import List, Optional

import aiofiles
import httpx
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from terabox_dl.models import (
    DownloadConfig,
    DownloadResult,
    DownloadStatus,
    FileInfo,
)
from terabox_dl.utils import sanitize_filename

console = Console()


class AsyncDownloader:
    """Handles concurrent downloading of TeraBox files with auto-retry and resuming."""

    def __init__(self, config: Optional[DownloadConfig] = None):
        self.config = config or DownloadConfig()

    async def download_file(
        self,
        file_info: FileInfo,
        progress: Optional[Progress] = None,
        task_id: Optional[TaskID] = None,
    ) -> DownloadResult:
        """Download a single file with automatic retry and range resume support."""
        os.makedirs(self.config.output_dir, exist_ok=True)
        safe_name = sanitize_filename(file_info.filename)
        target_path = os.path.join(self.config.output_dir, safe_name)
        part_path = target_path + ".part"

        # Check if file is already completed
        if os.path.exists(target_path) and os.path.getsize(target_path) > 0:
            existing_target_size = os.path.getsize(target_path)
            if (
                file_info.size_bytes > 0
                and existing_target_size == file_info.size_bytes
            ):
                if progress and task_id is not None:
                    progress.update(
                        task_id,
                        completed=existing_target_size,
                        total=existing_target_size,
                        description=f"[green]✓ {safe_name} (Existing)[/green]",
                    )
                return DownloadResult(
                    file_info=file_info,
                    status=DownloadStatus.COMPLETED,
                    file_path=target_path,
                    bytes_downloaded=existing_target_size,
                    attempts=0,
                )

        last_error: Optional[Exception] = None
        bytes_downloaded = 0

        async with httpx.AsyncClient(
            headers=file_info.headers,
            follow_redirects=True,
            timeout=httpx.Timeout(connect=15.0, read=30.0, write=30.0, pool=30.0),
        ) as client:
            for attempt in range(1, self.config.max_retries + 1):
                try:
                    existing_bytes = 0
                    if os.path.exists(part_path):
                        existing_bytes = os.path.getsize(part_path)

                    headers = dict(file_info.headers)
                    if existing_bytes > 0:
                        headers["Range"] = f"bytes={existing_bytes}-"

                    if progress and task_id is not None:
                        status_str = f"[cyan]{safe_name}[/cyan]"
                        if attempt > 1:
                            status_str += f" [yellow](Retry {attempt}/{self.config.max_retries})[/yellow]"
                        progress.update(
                            task_id,
                            completed=existing_bytes,
                            description=status_str,
                        )

                    async with client.stream(
                        "GET", file_info.download_url, headers=headers
                    ) as response:
                        # Handle 416 Range Not Satisfiable (file might already be finished)
                        if (
                            response.status_code == 416
                            and existing_bytes > 0
                            and (
                                file_info.size_bytes == 0
                                or existing_bytes >= file_info.size_bytes
                            )
                        ):
                            # Part file has all bytes
                            os.replace(part_path, target_path)
                            if progress and task_id is not None:
                                progress.update(
                                    task_id,
                                    completed=existing_bytes,
                                    description=f"[green]✓ {safe_name}[/green]",
                                )
                            return DownloadResult(
                                file_info=file_info,
                                status=DownloadStatus.COMPLETED,
                                file_path=target_path,
                                bytes_downloaded=existing_bytes,
                                attempts=attempt,
                            )

                        response.raise_for_status()

                        # Determine if server supported Range (HTTP 206) or reset to 200 OK
                        mode = "ab"
                        if response.status_code == 206 and existing_bytes > 0:
                            bytes_downloaded = existing_bytes
                        else:
                            # Server ignored range or started from zero
                            mode = "wb"
                            bytes_downloaded = 0
                            existing_bytes = 0

                        # Determine total size
                        total_bytes = file_info.size_bytes
                        content_length = response.headers.get("content-length")
                        if content_length and content_length.isdigit():
                            server_len = int(content_length)
                            if response.status_code == 206:
                                total_bytes = existing_bytes + server_len
                            else:
                                total_bytes = server_len

                        if progress and task_id is not None:
                            progress.update(
                                task_id,
                                total=total_bytes if total_bytes > 0 else None,
                                completed=bytes_downloaded,
                            )

                        async with aiofiles.open(part_path, mode) as f:
                            async for chunk in response.aiter_bytes(
                                chunk_size=self.config.chunk_size
                            ):
                                if chunk:
                                    await f.write(chunk)
                                    bytes_downloaded += len(chunk)
                                    if progress and task_id is not None:
                                        progress.update(
                                            task_id, completed=bytes_downloaded
                                        )

                    # Stream finished without error
                    if os.path.exists(part_path):
                        final_size = os.path.getsize(part_path)
                        if total_bytes > 0 and final_size < total_bytes:
                            raise IOError(
                                f"Incomplete download: {final_size}/{total_bytes} bytes"
                            )
                        os.replace(part_path, target_path)

                        if progress and task_id is not None:
                            progress.update(
                                task_id,
                                completed=final_size,
                                total=final_size,
                                description=f"[green]✓ {safe_name}[/green]",
                            )
                        return DownloadResult(
                            file_info=file_info,
                            status=DownloadStatus.COMPLETED,
                            file_path=target_path,
                            bytes_downloaded=final_size,
                            attempts=attempt,
                        )

                except (
                    httpx.RequestError,
                    httpx.HTTPStatusError,
                    IOError,
                    TimeoutError,
                ) as exc:
                    last_error = exc
                    if progress and task_id is not None:
                        progress.update(
                            task_id,
                            description=f"[red]! {safe_name} (Retry {attempt}/{self.config.max_retries})[/red]",
                        )
                    if attempt < self.config.max_retries:
                        delay = self.config.retry_delay * (2 ** (attempt - 1))
                        await asyncio.sleep(min(delay, 30.0))
                    else:
                        break

        # If we exhausted retries
        if progress and task_id is not None:
            progress.update(
                task_id,
                description=f"[red]✗ {safe_name} (Failed)[/red]",
            )

        return DownloadResult(
            file_info=file_info,
            status=DownloadStatus.FAILED,
            bytes_downloaded=bytes_downloaded,
            attempts=self.config.max_retries,
            error=str(last_error) if last_error else "Unknown error occurred",
        )

    async def download_all(self, files: List[FileInfo]) -> List[DownloadResult]:
        """Download multiple files concurrently using a semaphore and Rich progress."""
        if not files:
            return []

        os.makedirs(self.config.output_dir, exist_ok=True)
        semaphore = asyncio.Semaphore(self.config.concurrency)
        results: List[DownloadResult] = []

        class BinaryTransferSpeedColumn(TransferSpeedColumn):
            def render(self, task) -> "Text":
                speed = task.finished_speed or task.speed
                if speed is None:
                    from rich.text import Text
                    return Text("?", style="progress.data.speed")
                from terabox_dl.utils import format_bytes
                from rich.text import Text
                data_speed = format_bytes(int(speed))
                return Text(f"{data_speed}/s", style="progress.data.speed")

        progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            DownloadColumn(binary_units=True),
            BinaryTransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
        )

        async def _worker(file_info: FileInfo) -> DownloadResult:
            async with semaphore:
                safe_name = sanitize_filename(file_info.filename)
                task_id = progress.add_task(
                    f"[cyan]{safe_name}[/cyan]",
                    total=file_info.size_bytes if file_info.size_bytes > 0 else None,
                )
                res = await self.download_file(file_info, progress=progress, task_id=task_id)
                progress.remove_task(task_id)
                if res.status == DownloadStatus.COMPLETED:
                    progress.console.print(f"[green]✓ Completed:[/green] {safe_name}")
                else:
                    progress.console.print(f"[red]✗ Failed:[/red] {safe_name} ({res.error})")
                return res

        with progress:
            tasks = [_worker(f) for f in files]
            results = await asyncio.gather(*tasks)

        return results

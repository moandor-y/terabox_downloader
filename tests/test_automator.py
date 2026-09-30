"""Unit tests for automator.py."""

import json
import logging
from typing import List

import pytest
from terabox_dl import automator
from terabox_dl.automator import (
    MAX_LIST_PAGES,
    TeraBoxAutomator,
    build_listing_script,
    check_sandbox_policy,
    consume_listing_result,
    dedupe_files,
    parse_dom_files,
    parse_proxy_json,
    should_disable_sandbox,
)
from terabox_dl.models import DownloadConfig, FileInfo


def test_parse_proxy_json_success():
    sample_json = {
        "errno": 0,
        "list": [
            {
                "fs_id": "1001",
                "server_filename": "movie_sample.mp4",
                "size": 104857600,
                "isdir": 0,
                "dlink": "https://d.1024teradl.com/download/movie_sample.mp4",
            },
            {
                "fs_id": "1002",
                "server_filename": "subfolder",
                "size": 0,
                "isdir": 1,
                "dlink": "",
            },
        ],
    }

    files = parse_proxy_json(sample_json)
    assert len(files) == 1
    assert files[0].filename == "movie_sample.mp4"
    assert files[0].size_bytes == 104857600
    assert files[0].download_url == "https://d.1024teradl.com/download/movie_sample.mp4"
    assert files[0].fs_id == "1001"
    assert files[0].isdir is False


def test_parse_proxy_json_direct_link():
    sample_json = {
        "errno": 0,
        "list": [
            {
                "fs_id": 1062221837712075,
                "server_filename": "2B9131FD6E605560-00-00.mrimg.000.enc",
                "size": 4290773051,
                "formatted_size": "4.00 GB",
                "direct_link": "https://dl-worker.teraboxdl.site?token=test_token_1",
                "isdir": 0,
            },
            {
                "fs_id": 972772508297927,
                "server_filename": "2B9131FD6E605560-00-00.mrimg.001.enc",
                "size": 4290773051,
                "formatted_size": "4.00 GB",
                "direct_link": "https://dl-worker.teraboxdl.site?token=test_token_2",
                "isdir": 0,
            },
        ],
    }

    files = parse_proxy_json(sample_json)
    assert len(files) == 2
    assert files[0].filename == "2B9131FD6E605560-00-00.mrimg.000.enc"
    assert files[0].download_url == "https://dl-worker.teraboxdl.site?token=test_token_1"
    assert files[0].size_bytes == 4290773051
    assert files[1].filename == "2B9131FD6E605560-00-00.mrimg.001.enc"
    assert files[1].download_url == "https://dl-worker.teraboxdl.site?token=test_token_2"


def test_parse_proxy_json_error():
    err_json = {
        "errno": 140,
        "errmsg": "File is not accessible due to TeraBox restrictions",
    }
    with pytest.raises(RuntimeError, match="TeraBox API Error .140."):
        parse_proxy_json(err_json)


def test_parse_dom_files():
    sample_html = """
    <div class="card">
        <h3>vacation_video.mp4</h3>
        <span>Size: 25.5 MB</span>
        <a href="https://d.1024teradl.com/dl/vacation_video.mp4">Download Now</a>
    </div>
    <div class="card">
        <a href="https://1024teradl.com/faq">FAQ</a>
    </div>
    """
    files = parse_dom_files(sample_html)
    assert len(files) == 1
    assert files[0].filename == "vacation_video.mp4"
    assert files[0].download_url == "https://d.1024teradl.com/dl/vacation_video.mp4"
    assert files[0].size_bytes == int(25.5 * 1024**2)


def test_parse_dom_files_filtering():
    sample_html = """
    <div>
        <a href="https://d.1024teradl.com/dl/file1.mp4">file1.mp4</a>
        <a href="https://fast.1024teradl.com/dl/archive.zip">archive.zip</a>
        <a href="https://teraboxapi.com">API documentation</a>
        <a href="https://viral.teraboxdl.site">Viral site</a>
        <a href="https://t.me/channel">Telegram</a>
        <a href="https://instagram.com/page">IG</a>
        <a href="https://1024teradl.com/privacy">Privacy</a>
    </div>
    """
    files = parse_dom_files(sample_html)
    assert len(files) == 2
    filenames = {f.filename for f in files}
    assert filenames == {"file1.mp4", "archive.zip"}


@pytest.mark.asyncio
async def test_extract_files_invalid_url():
    automator = TeraBoxAutomator()
    with pytest.raises(ValueError, match="Invalid TeraBox shared link"):
        await automator.extract_files("https://google.com/not_a_terabox_link")


def test_parse_proxy_json_multi_page():
    page1_json = {
        "errno": 0,
        "list": [
            {
                "fs_id": f"100{i}",
                "server_filename": f"part_{i}.bin",
                "size": 1024,
                "isdir": 0,
                "dlink": f"https://d.1024teradl.com/download/part_{i}.bin",
            }
            for i in range(20)
        ],
    }
    page2_json = {
        "errno": 0,
        "list": [
            {
                "fs_id": f"200{i}",
                "server_filename": f"part_{i+20}.bin",
                "size": 1024,
                "isdir": 0,
                "dlink": f"https://d.1024teradl.com/download/part_{i+20}.bin",
            }
            for i in range(20)
        ],
    }

    files1 = parse_proxy_json(page1_json)
    files2 = parse_proxy_json(page2_json)
    all_files = files1 + files2

    assert len(all_files) == 40
    assert all_files[0].filename == "part_0.bin"
    assert all_files[39].filename == "part_39.bin"


def _batch(start: int, count: int) -> dict:
    return {
        "errno": 0,
        "list": [
            {
                "fs_id": str(1000 + i),
                "server_filename": f"part_{i}.bin",
                "size": 1024,
                "isdir": 0,
                "dlink": f"https://d.1024teradl.com/download/part_{i}.bin",
            }
            for i in range(start, start + count)
        ],
    }


def test_consume_listing_result_merges_all_pages():
    raw = json.dumps(
        {"batches": [_batch(0, 20), _batch(20, 20), _batch(40, 7)], "pages": 3, "folders": 0}
    )
    captured: List[FileInfo] = []

    assert consume_listing_result(raw, captured) is None
    assert len(captured) == 47
    assert captured[-1].filename == "part_46.bin"


def test_consume_listing_result_warns_when_truncated(caplog):
    raw = json.dumps(
        {
            "batches": [_batch(0, 20)],
            "pages": 1,
            "folders": 0,
            "truncated": True,
            "reason": "request for page 2 failed: HTTP 429",
        }
    )
    captured: List[FileInfo] = []

    with caplog.at_level(logging.WARNING):
        assert consume_listing_result(raw, captured) is None

    assert len(captured) == 20
    assert "INCOMPLETE" in caplog.text
    assert "HTTP 429" in caplog.text


def test_consume_listing_result_returns_api_error():
    raw = json.dumps({"error": "File is not accessible"})
    captured: List[FileInfo] = []

    assert consume_listing_result(raw, captured) == "File is not accessible"
    assert captured == []


def test_consume_listing_result_ignores_bad_payload():
    captured: List[FileInfo] = []

    assert consume_listing_result("not json", captured) is None
    assert consume_listing_result(None, captured) is None
    assert captured == []


def test_dedupe_files_uses_fs_id_identity():
    files = [
        FileInfo(filename="a.bin", download_url="https://d/a", fs_id="1"),
        # Same file seen again on an overlapping page, with a refreshed link.
        FileInfo(filename="a.bin", download_url="https://d/a?t=2", fs_id="1"),
        # Distinct files that momentarily share a generic link must both survive.
        FileInfo(filename="b.bin", download_url="https://d/same", fs_id="2"),
        FileInfo(filename="c.bin", download_url="https://d/same", fs_id="3"),
        # No fs_id: fall back to URL identity.
        FileInfo(filename="d.bin", download_url="https://d/d"),
        FileInfo(filename="d.bin", download_url="https://d/d"),
        # Unusable entry.
        FileInfo(filename="e.bin", download_url=""),
    ]

    unique = dedupe_files(files)

    assert [f.filename for f in unique] == ["a.bin", "b.bin", "c.bin", "d.bin"]
    assert unique[0].download_url == "https://d/a"


def test_build_listing_script_has_no_placeholders():
    script = build_listing_script("https://terabox.com/s/1abc")

    assert "__TERABOX_URL__" not in script
    assert "__MAX_PAGES__" not in script
    assert "__MAX_ATTEMPTS__" not in script
    assert "__DELAY_MS__" not in script
    assert '"https://terabox.com/s/1abc"' in script
    assert str(MAX_LIST_PAGES) in script


def test_build_listing_script_escapes_url():
    script = build_listing_script('https://terabox.com/s/"; alert(1); //')

    assert '\\"; alert(1); //' in script


def test_sandbox_stays_enabled_for_non_root(monkeypatch):
    monkeypatch.setattr(automator.os, "geteuid", lambda: 1000, raising=False)

    assert should_disable_sandbox(allow_no_sandbox=False) is False
    assert should_disable_sandbox(allow_no_sandbox=True) is False


def test_root_without_optin_is_refused(monkeypatch):
    monkeypatch.setattr(automator.os, "geteuid", lambda: 0, raising=False)

    with pytest.raises(RuntimeError, match="Refusing to launch Chrome as root"):
        should_disable_sandbox(allow_no_sandbox=False)
    with pytest.raises(RuntimeError, match="Refusing to launch Chrome as root"):
        check_sandbox_policy(allow_no_sandbox=False)


def test_root_with_optin_disables_sandbox_and_warns(monkeypatch, caplog):
    monkeypatch.setattr(automator.os, "geteuid", lambda: 0, raising=False)

    with caplog.at_level(logging.WARNING):
        assert should_disable_sandbox(allow_no_sandbox=True) is True

    assert "WITHOUT the sandbox" in caplog.text


async def test_extract_files_refuses_root_before_launching_browser(monkeypatch):
    """The 'auto' engine must not fall back to playwright after a root refusal."""
    monkeypatch.setattr(automator.os, "geteuid", lambda: 0, raising=False)
    calls: List[str] = []

    async def _fail(self, url):
        calls.append("launched")
        raise AssertionError("browser must not be launched as root")

    monkeypatch.setattr(TeraBoxAutomator, "_extract_files_nodriver", _fail)
    monkeypatch.setattr(TeraBoxAutomator, "_extract_files_playwright", _fail)

    config = DownloadConfig(browser_engine="auto", allow_no_sandbox=False)
    with pytest.raises(RuntimeError, match="Refusing to launch Chrome as root"):
        await TeraBoxAutomator(config).extract_files("https://terabox.com/s/1abc")

    assert calls == []


def test_consume_listing_result_sets_truncated_on_extracted_file_list():
    raw = json.dumps(
        {
            "batches": [_batch(0, 5)],
            "pages": 1,
            "folders": 0,
            "truncated": True,
            "reason": "request for page 2 failed: HTTP 503",
        }
    )
    captured = automator.ExtractedFileList()
    assert consume_listing_result(raw, captured) is None
    assert len(captured) == 5
    assert captured.truncated is True
    assert captured.reason == "request for page 2 failed: HTTP 503"
    assert captured.truncation_reason == "request for page 2 failed: HTTP 503"


def test_dedupe_files_preserves_truncation_metadata():
    source = automator.ExtractedFileList(
        [
            FileInfo(filename="a.bin", download_url="https://d/a", fs_id="1"),
            FileInfo(filename="a.bin", download_url="https://d/a2", fs_id="1"),
        ],
        truncated=True,
        reason="reached page cap",
    )
    deduped = dedupe_files(source)
    assert isinstance(deduped, automator.ExtractedFileList)
    assert len(deduped) == 1
    assert deduped.truncated is True
    assert deduped.reason == "reached page cap"
    assert deduped.truncation_reason == "reached page cap"


def test_cloudflare_detection_helpers():
    assert automator.is_cloudflare_title("Just a moment...") is True
    assert automator.is_cloudflare_title("Attention Required! | Cloudflare") is True
    assert automator.is_cloudflare_title("1024teradl.com - TeraBox Downloader") is False
    assert automator.is_cloudflare_title(None) is False

    assert automator.is_cloudflare_html('<div id="challenge-running">Verify you are human</div>') is True
    assert automator.is_cloudflare_html("<html><body>Sorry, you have been blocked</body></html>") is True
    assert automator.is_cloudflare_html("<html><body><input placeholder='Paste Terabox link'></body></html>") is False

    assert automator.is_cloudflare_challenge_or_block("Just a moment...", "") is True
    assert automator.is_cloudflare_challenge_or_block("1024teradl", "", has_input=False, has_challenge_element=True) is True
    assert automator.is_cloudflare_challenge_or_block("1024teradl", "", has_input=True, has_challenge_element=True) is False

    assert automator.is_cloudflare_error(automator.CloudflareBlockError("blocked")) is True
    assert automator.is_cloudflare_error(RuntimeError("Blocked by Cloudflare Turnstile")) is True
    assert automator.is_cloudflare_error(RuntimeError("TeraBox API Error (140): restricted")) is False


@pytest.mark.asyncio
async def test_extract_files_nodriver_cloudflare_challenge_timeout_raises(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    monkeypatch.setattr(automator.os, "geteuid", lambda: 1000, raising=False)

    fake_iframe = MagicMock()
    fake_iframe.mouse_click = AsyncMock()

    fake_page = MagicMock()
    fake_page.evaluate = AsyncMock(return_value="Just a moment...")
    fake_page.select = AsyncMock(return_value=fake_iframe)
    fake_page.get_content = AsyncMock(
        return_value='<html><head><title>Just a moment...</title></head><body><div id="challenge-running">Verify you are human</div></body></html>'
    )

    fake_browser = MagicMock()
    fake_browser.get = AsyncMock(return_value=fake_page)
    fake_browser.aclose = AsyncMock()
    fake_browser.stop = MagicMock()

    import nodriver as uc

    monkeypatch.setattr(uc, "start", AsyncMock(return_value=fake_browser))

    config = DownloadConfig(
        browser_engine="nodriver",
        challenge_timeout_attempts=2,
        challenge_poll_interval=0.0,
    )
    with pytest.raises(automator.CloudflareBlockError, match="Cloudflare"):
        await TeraBoxAutomator(config).extract_files("https://terabox.com/s/1abc")

    fake_browser.stop.assert_called_once()


@pytest.mark.asyncio
async def test_extract_files_playwright_cloudflare_challenge_timeout_raises(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    monkeypatch.setattr(automator.os, "geteuid", lambda: 1000, raising=False)

    fake_page = MagicMock()
    fake_page.on = MagicMock()
    fake_page.goto = AsyncMock()
    fake_page.title = AsyncMock(return_value="Just a moment...")
    fake_page.query_selector = AsyncMock(return_value=None)
    fake_page.frames = []
    fake_page.content = AsyncMock(
        return_value='<html><head><title>Just a moment...</title></head><body><div id="challenge-running"></div></body></html>'
    )

    fake_context = MagicMock()
    fake_context.new_page = AsyncMock(return_value=fake_page)

    fake_browser = MagicMock()
    fake_browser.new_context = AsyncMock(return_value=fake_context)
    fake_browser.close = AsyncMock()

    fake_pw = MagicMock()
    fake_pw.chromium.launch = AsyncMock(return_value=fake_browser)

    fake_pw_cm = MagicMock()
    fake_pw_cm.__aenter__ = AsyncMock(return_value=fake_pw)
    fake_pw_cm.__aexit__ = AsyncMock(return_value=False)

    import playwright.async_api
    import playwright_stealth

    monkeypatch.setattr(playwright.async_api, "async_playwright", lambda: fake_pw_cm)
    if hasattr(playwright_stealth, "Stealth"):
        monkeypatch.setattr(
            playwright_stealth.Stealth, "apply_stealth_async", AsyncMock()
        )

    config = DownloadConfig(
        browser_engine="playwright",
        challenge_timeout_attempts=2,
        challenge_poll_interval=0.0,
    )
    with pytest.raises(automator.CloudflareBlockError, match="Cloudflare"):
        await TeraBoxAutomator(config).extract_files("https://terabox.com/s/1abc")

    fake_browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_files_auto_cloudflare_block_across_engines_raises(monkeypatch):
    monkeypatch.setattr(automator.os, "geteuid", lambda: 1000, raising=False)
    calls: List[str] = []

    async def _nodriver_cf(self, url):
        calls.append("nodriver")
        raise automator.CloudflareBlockError("nodriver stuck on Cloudflare Turnstile")

    async def _playwright_cf(self, url):
        calls.append("playwright")
        raise automator.CloudflareBlockError("playwright stuck on Cloudflare Turnstile")

    monkeypatch.setattr(TeraBoxAutomator, "_extract_files_nodriver", _nodriver_cf)
    monkeypatch.setattr(TeraBoxAutomator, "_extract_files_playwright", _playwright_cf)

    config = DownloadConfig(browser_engine="auto")
    with pytest.raises(automator.CloudflareBlockError, match="Cloudflare"):
        await TeraBoxAutomator(config).extract_files("https://terabox.com/s/1abc")

    assert calls == ["nodriver", "playwright"]


@pytest.mark.asyncio
async def test_extract_files_auto_preserves_cloudflare_error_when_playwright_unavailable(
    monkeypatch,
):
    monkeypatch.setattr(automator.os, "geteuid", lambda: 1000, raising=False)

    async def _nodriver_cf(self, url):
        raise automator.CloudflareBlockError("nodriver blocked by Cloudflare")

    async def _playwright_unavailable(self, url):
        raise RuntimeError("Playwright browser binary not installed")

    monkeypatch.setattr(TeraBoxAutomator, "_extract_files_nodriver", _nodriver_cf)
    monkeypatch.setattr(
        TeraBoxAutomator, "_extract_files_playwright", _playwright_unavailable
    )

    config = DownloadConfig(browser_engine="auto")
    with pytest.raises(automator.CloudflareBlockError, match="nodriver blocked by Cloudflare"):
        await TeraBoxAutomator(config).extract_files("https://terabox.com/s/1abc")


@pytest.mark.asyncio
async def test_extract_files_tracks_and_resets_truncation_state(monkeypatch):
    monkeypatch.setattr(automator.os, "geteuid", lambda: 1000, raising=False)

    f1 = FileInfo(filename="part1.bin", download_url="https://d/1", fs_id="1")
    f2 = FileInfo(filename="part2.bin", download_url="https://d/2", fs_id="2")
    responses = [
        automator.ExtractedFileList([f1], truncated=True, reason="page 2 failed"),
        automator.ExtractedFileList([f1, f2], truncated=False),
    ]

    async def _fake_nodriver(self, url):
        return responses.pop(0)

    monkeypatch.setattr(TeraBoxAutomator, "_extract_files_nodriver", _fake_nodriver)

    auto = TeraBoxAutomator(DownloadConfig(browser_engine="nodriver"))
    res1 = await auto.extract_files("https://terabox.com/s/1abc")
    assert res1.truncated is True
    assert auto.last_listing_truncated is True
    assert auto.last_truncation_reason == "page 2 failed"

    res2 = await auto.extract_files("https://terabox.com/s/1abc")
    assert res2.truncated is False
    assert auto.last_listing_truncated is False
    assert auto.last_truncation_reason is None


@pytest.mark.asyncio
async def test_extract_files_playwright_repeating_turnstile_clicks_fast_fails(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    monkeypatch.setattr(automator.os, "geteuid", lambda: 1000, raising=False)

    fake_el = MagicMock()
    fake_el.bounding_box = AsyncMock(
        return_value={"x": 100.0, "y": 200.0, "width": 300.0, "height": 65.0}
    )

    fake_page = MagicMock()
    fake_page.on = MagicMock()
    fake_page.goto = AsyncMock()
    fake_page.title = AsyncMock(return_value="Just a moment...")
    fake_page.query_selector = AsyncMock(return_value=fake_el)
    fake_page.frames = []
    fake_page.mouse = MagicMock()
    fake_page.mouse.move = AsyncMock()
    fake_page.mouse.click = AsyncMock()
    fake_page.content = AsyncMock(
        return_value='<html><head><title>Just a moment...</title></head><body><div id="challenge-running"></div></body></html>'
    )

    fake_context = MagicMock()
    fake_context.new_page = AsyncMock(return_value=fake_page)

    fake_browser = MagicMock()
    fake_browser.new_context = AsyncMock(return_value=fake_context)
    fake_browser.close = AsyncMock()

    fake_pw = MagicMock()
    fake_pw.chromium.launch = AsyncMock(return_value=fake_browser)

    fake_pw_cm = MagicMock()
    fake_pw_cm.__aenter__ = AsyncMock(return_value=fake_pw)
    fake_pw_cm.__aexit__ = AsyncMock(return_value=False)

    import playwright.async_api
    import playwright_stealth

    monkeypatch.setattr(playwright.async_api, "async_playwright", lambda: fake_pw_cm)
    if hasattr(playwright_stealth, "Stealth"):
        monkeypatch.setattr(
            playwright_stealth.Stealth, "apply_stealth_async", AsyncMock()
        )

    config = DownloadConfig(
        browser_engine="playwright",
        challenge_timeout_attempts=30,
        challenge_poll_interval=0.0,
        challenge_max_clicks=2,
        challenge_click_cooldown_attempts=3,
    )
    with pytest.raises(
        automator.CloudflareBlockError,
        match="Turnstile challenge repeated after 2 click attempts",
    ):
        await TeraBoxAutomator(config).extract_files("https://terabox.com/s/1abc")

    # Clicked only twice (at poll 0 and poll 3), then aborted early at poll 6 instead of polling 30 times
    assert fake_page.mouse.click.await_count == 2
    fake_page.mouse.click.assert_called_with(128.0, 232.5)
    assert fake_page.title.await_count == 7
    fake_browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_files_playwright_turnstile_click_resolves_after_cooldown(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    monkeypatch.setattr(automator.os, "geteuid", lambda: 1000, raising=False)

    fake_el = MagicMock()
    fake_el.bounding_box = AsyncMock(
        return_value={"x": 50.0, "y": 100.0, "width": 300.0, "height": 65.0}
    )

    titles = ["Just a moment...", "Just a moment...", "1024teradl.com"]
    fake_page = MagicMock()
    fake_page.on = MagicMock()
    fake_page.goto = AsyncMock()
    fake_page.title = AsyncMock(side_effect=lambda: titles.pop(0) if titles else "1024teradl.com")
    fake_page.query_selector = AsyncMock(return_value=fake_el)
    fake_page.wait_for_selector = AsyncMock(return_value=MagicMock())
    fake_page.frames = []
    fake_page.mouse = MagicMock()
    fake_page.mouse.move = AsyncMock()
    fake_page.mouse.click = AsyncMock()
    fake_page.evaluate = AsyncMock(
        return_value=json.dumps({"batches": [_batch(0, 1)], "pages": 1, "folders": 0})
    )

    fake_context = MagicMock()
    fake_context.new_page = AsyncMock(return_value=fake_page)

    fake_browser = MagicMock()
    fake_browser.new_context = AsyncMock(return_value=fake_context)
    fake_browser.close = AsyncMock()

    fake_pw = MagicMock()
    fake_pw.chromium.launch = AsyncMock(return_value=fake_browser)

    fake_pw_cm = MagicMock()
    fake_pw_cm.__aenter__ = AsyncMock(return_value=fake_pw)
    fake_pw_cm.__aexit__ = AsyncMock(return_value=False)

    import playwright.async_api
    import playwright_stealth

    monkeypatch.setattr(playwright.async_api, "async_playwright", lambda: fake_pw_cm)
    if hasattr(playwright_stealth, "Stealth"):
        monkeypatch.setattr(
            playwright_stealth.Stealth, "apply_stealth_async", AsyncMock()
        )

    config = DownloadConfig(
        browser_engine="playwright",
        challenge_timeout_attempts=10,
        challenge_poll_interval=0.0,
        challenge_max_clicks=3,
        challenge_click_cooldown_attempts=4,
    )
    files = await TeraBoxAutomator(config).extract_files("https://terabox.com/s/1abc")
    assert len(files) == 1
    # Clicked once on poll 0, waited without re-clicking on poll 1, and resolved on poll 2
    assert fake_page.mouse.click.await_count == 1
    fake_page.mouse.click.assert_called_once_with(78.0, 132.5)


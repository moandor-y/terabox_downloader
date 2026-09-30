"""Browser automation for 1024teradl.com using nodriver / playwright."""

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from terabox_dl.models import (
    CloudflareBlockError,
    DownloadConfig,
    ExtractedFileList,
    FileInfo,
    FileList,
    FileListing,
    SandboxPolicyError,
)
from terabox_dl.utils import is_valid_terabox_url, parse_size, sanitize_filename

logger = logging.getLogger(__name__)

CLOUDFLARE_TITLE_MARKERS = (
    "just a moment",
    "cloudflare",
    "attention required",
    "access denied",
    "security check",
    "verify you are human",
    "verifying you are human",
    "ddos-guard",
    "one more step",
)

CLOUDFLARE_HTML_MARKERS = (
    "attention required! | cloudflare",
    "sorry, you have been blocked",
    "you are unable to access",
    "cf-error-details",
    "cf-chl-bypass",
    "cf-browser-verification",
    'id="challenge-running"',
    "id='challenge-running'",
    'id="challenge-error-title"',
    "id='challenge-error-title'",
    'id="challenge-stage"',
    "id='challenge-stage'",
    "verify you are human",
    "verifying you are human",
    "checking if the site connection is secure",
    "enable javascript and cookies to continue",
    "__cf_chl_",
    "_cf_chl_opt",
    "cdn-cgi/challenge-platform",
    "challenges.cloudflare.com",
)


def is_cloudflare_title(title: Optional[str]) -> bool:
    """Return True if a page title indicates a Cloudflare challenge or block page."""
    if not title or not isinstance(title, str):
        return False
    lowered = title.strip().lower()
    return any(marker in lowered for marker in CLOUDFLARE_TITLE_MARKERS)


def is_cloudflare_html(html: Optional[str]) -> bool:
    """Return True if HTML content indicates a Cloudflare challenge or block page."""
    if not html or not isinstance(html, str):
        return False
    lowered = html.lower()
    if any(marker in lowered for marker in CLOUDFLARE_HTML_MARKERS):
        return True
    if "<title>" in lowered and any(marker in lowered for marker in CLOUDFLARE_TITLE_MARKERS):
        return True
    return False


def is_cloudflare_challenge_or_block(
    title: Optional[str] = None,
    html: Optional[str] = None,
    has_input: bool = False,
    has_challenge_element: bool = False,
) -> bool:
    """Return True if page title, HTML, or challenge elements indicate a Cloudflare block/challenge."""
    if is_cloudflare_title(title):
        return True
    if is_cloudflare_html(html):
        return True
    if has_challenge_element and not has_input:
        return True
    return False


def is_cloudflare_error(exc: BaseException) -> bool:
    """Return True if an exception represents a Cloudflare bot-detection or Turnstile block."""
    if isinstance(exc, CloudflareBlockError):
        return True
    msg = str(exc).lower()
    return any(
        marker in msg
        for marker in (
            "cloudflare",
            "turnstile",
            "just a moment",
            "attention required",
            "verify you are human",
            "verifying you are human",
            "cf-chl",
            "cf-error",
            "bot detection",
            "sorry, you have been blocked",
            "access denied",
        )
    )

# Common file extensions for direct download links
FILE_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".mp3", ".wav", ".aac", ".flac", ".ogg",
    ".zip", ".rar", ".7z", ".tar", ".gz", ".iso",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg",
    ".apk", ".exe", ".dmg", ".pkg",
}

IGNORE_DOMAINS = {
    "google.com",
    "googlesyndication.com",
    "cloudflare.com",
    "umami.is",
    "instagram.com",
    "tiktok.com",
    "facebook.com",
    "diskwala.com",
    "teraboxapi.com",
    "viral.teraboxdl.site",
    "t.me",
    "telegram.me",
    "discord.gg",
    "twitter.com",
    "x.com",
    "reddit.com",
}

AD_BLOCKED_PATTERNS = [
    "*googlesyndication*",
    "*doubleclick.net*",
    "*adservice*",
    "*adsterra*",
    "*monetag*",
    "*popcash*",
    "*popads*",
    "*adnxs*",
    "*adtrue*",
    "*propellerads*",
    "*onclickalgo*",
    "*clksite*",
    "*vignette*",
    "*adkeeper*",
    "*highperformanceformat*",
    "*effectivegate*",
    "*alwingulla*",
    "*deloton*",
    "*syndication*",
    "*creative.revcontent.com*",
]

# Chrome cannot enable its setuid/namespace sandbox when running as root, so the
# only way to launch as root is with the sandbox off. That sandbox is the main
# containment boundary between a renderer exploit and the host, and this tool
# deliberately loads an untrusted, ad-funded third-party page and runs script in
# its origin. Silently disabling it therefore turns a malicious ad or renderer
# bug into root-level code execution, so it is refused unless opted into.
ROOT_SANDBOX_ERROR = (
    "Refusing to launch Chrome as root: Chrome cannot enable its sandbox for the "
    "root user, and this tool renders an untrusted third-party page with ads. "
    "Without the sandbox, a renderer exploit would run as root on this machine.\n"
    "Re-run as a non-root user. If this is a disposable container and you accept "
    "that risk, pass --i-accept-no-sandbox."
)


def is_running_as_root() -> bool:
    """Report whether this process has an effective UID of 0 (POSIX only)."""
    return hasattr(os, "geteuid") and os.geteuid() == 0


def check_sandbox_policy(allow_no_sandbox: bool) -> None:
    """Raise if Chrome could only be launched with its sandbox disabled.

    Kept separate from should_disable_sandbox so callers can fail fast before
    starting any browser without emitting the launch-time warning twice.
    """
    if is_running_as_root() and not allow_no_sandbox:
        raise SandboxPolicyError(ROOT_SANDBOX_ERROR)


def should_disable_sandbox(allow_no_sandbox: bool) -> bool:
    """Return whether Chrome must be launched without its sandbox.

    Raises RuntimeError when running as root without an explicit opt-in. When the
    caller has opted in, the degraded state is logged loudly rather than hidden.
    """
    check_sandbox_policy(allow_no_sandbox)
    if not is_running_as_root():
        return False
    logger.warning(
        "Launching Chrome as root WITHOUT the sandbox because --i-accept-no-sandbox "
        "was given. A malicious ad or renderer exploit on the extraction page can "
        "execute as root on this host."
    )
    return True


def parse_proxy_json(json_data: Dict[str, Any]) -> List[FileInfo]:
    """Parse JSON response from 1024teradl.com /api/proxy into FileInfo objects."""
    if not isinstance(json_data, dict):
        return []

    errno = json_data.get("errno", 0)
    if errno != 0:
        errmsg = json_data.get("errmsg") or json_data.get("msg") or f"Error code {errno}"
        raise RuntimeError(f"TeraBox API Error ({errno}): {errmsg}")

    files: List[FileInfo] = []
    items = (
        json_data.get("list")
        or json_data.get("files")
        or json_data.get("data")
        or []
    )
    if isinstance(items, dict):
        items = items.get("list") or items.get("files") or [items]
    elif not isinstance(items, list):
        items = []

    if not items and ("dlink" in json_data or "download_url" in json_data or "direct_link" in json_data):
        items = [json_data]

    for item in items:
        if not isinstance(item, dict):
            continue
        isdir = bool(item.get("isdir", 0))
        filename = (
            item.get("server_filename")
            or item.get("filename")
            or item.get("name")
            or item.get("title")
            or "unnamed_file"
        )
        download_url = (
            item.get("direct_link")
            or item.get("fast_download_url")
            or item.get("dlink")
            or item.get("download_url")
            or item.get("url")
            or item.get("link")
            or ""
        )
        size_raw = item.get("size") or item.get("size_bytes") or 0
        try:
            size_bytes = int(size_raw)
        except (ValueError, TypeError):
            size_bytes = 0

        fs_id = str(item.get("fs_id", "")) or None

        if download_url and not isdir:
            files.append(
                FileInfo(
                    filename=sanitize_filename(str(filename)),
                    download_url=str(download_url),
                    size_bytes=size_bytes,
                    fs_id=fs_id,
                    isdir=isdir,
                )
            )
        elif not isdir and not download_url:
            logger.warning(f"File '{filename}' was found, but 1024teradl.com failed to generate a direct download link. It may be too large or restricted.")

    return files


def parse_dom_files(html: str) -> List[FileInfo]:
    """Parse HTML DOM of 1024teradl.com to extract genuine generated file download links."""
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    files: List[FileInfo] = []
    seen_urls: Set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue

        try:
            parsed = urlparse(href)
            domain = parsed.netloc.lower()
            if any(d in domain for d in IGNORE_DOMAINS):
                continue
        except Exception:
            continue

        if any(
            x in href.lower()
            for x in ["/faq", "/privacy", "/terms", "/about", "/contact", "/login", "/signup", "/docs"]
        ):
            continue

        is_dl_domain = any(
            x in domain
            for x in ["d.1024teradl", "fast.1024teradl", "dl.terabox", "dlink", "terafile", "dl-worker", "teraboxdl"]
        )
        has_file_ext = any(href.lower().split("?")[0].endswith(ext) for ext in FILE_EXTENSIONS)

        if not (is_dl_domain or has_file_ext or "dlink=" in href.lower()):
            continue

        text_label = a.get_text(strip=True)
        filename = "downloaded_file"
        size_bytes = 0

        if text_label and any(text_label.lower().endswith(ext) for ext in FILE_EXTENSIONS):
            filename = text_label
        elif text_label and len(text_label) > 3 and "download" not in text_label.lower():
            filename = text_label
        else:
            card = a.find_parent(["li", "tr", "article", "div"])
            if card and len(card.find_all("a")) == 1:
                card_text = card.get_text(" ", strip=True)
                ext_match = re.search(
                    r"([a-zA-Z0-9_\-\.\s\(\)]+\.(?:mp4|mkv|avi|zip|rar|pdf|doc|docx|jpg|png|mp3|iso|tar|gz))",
                    card_text,
                    re.IGNORECASE,
                )
                if ext_match:
                    filename = ext_match.group(1).strip()

        # Try size extraction from parent
        card = a.find_parent(["li", "tr", "article", "div"])
        if card:
            card_text = card.get_text(" ", strip=True)
            size_match = re.search(
                r"(\d+(?:\.\d+)?\s*(?:KB|MB|GB|TB|B|bytes))",
                card_text,
                re.IGNORECASE,
            )
            if size_match:
                size_bytes = parse_size(size_match.group(1))

        if href not in seen_urls:
            seen_urls.add(href)
            files.append(
                FileInfo(
                    filename=sanitize_filename(filename),
                    download_url=href,
                    size_bytes=size_bytes,
                )
            )

    return files


# Safety cap on the total number of /api/proxy list pages fetched for one share.
MAX_LIST_PAGES = 200
# Per-request retry attempts before giving up on a list page.
LIST_REQUEST_ATTEMPTS = 3
# Politeness delay between consecutive list page requests (avoids rate limiting).
LIST_REQUEST_DELAY_MS = 150

# In-page listing script executed inside the 1024teradl.com origin.
#
# It walks every page of the share (and every nested folder) via /api/proxy and
# returns all raw batches plus diagnostics. Robustness rules:
#   * transient fetch failures are retried instead of silently ending the walk;
#   * pagination continues while pages come back full, even when the server
#     omits or wrongly reports `has_more` (the cause of truncation at an exact
#     multiple of the page size);
#   * a page that yields no unseen items stops the walk for that folder, so a
#     server that ignores `page` cannot loop forever;
#   * if the walk ends early, `truncated`/`reason` say so instead of pretending
#     the partial listing is complete.
_LISTING_SCRIPT_TEMPLATE = """
(async () => {
    const teraboxUrl = __TERABOX_URL__;
    const MAX_PAGES = __MAX_PAGES__;
    const MAX_ATTEMPTS = __MAX_ATTEMPTS__;
    const DELAY_MS = __DELAY_MS__;
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

    const batches = [];
    const seenItems = new Set();
    // '' is the share root; nested folder paths are appended as they are found.
    const pendingDirs = [''];
    const knownDirs = new Set(['']);
    let shareId = null;
    let uk = null;
    let pagesFetched = 0;
    let truncated = false;
    let reason = '';

    const fetchPage = async (dir, page) => {
        let lastError = '';
        for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
            try {
                const payload = { url: teraboxUrl, page: page };
                if (shareId && uk) {
                    payload.share_id = shareId;
                    payload.uk = uk;
                    payload.dir = dir;
                }
                const r = await fetch('/api/proxy', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                if (r.ok) {
                    return { data: await r.json() };
                }
                lastError = 'HTTP ' + r.status;
                // Client errors other than rate limiting will not succeed on retry.
                if (r.status >= 400 && r.status < 500 && r.status !== 429) {
                    return { error: lastError };
                }
            } catch (e) {
                lastError = String(e);
            }
            await sleep(DELAY_MS * 4 * attempt);
        }
        return { error: lastError || 'request failed' };
    };

    walk:
    while (pendingDirs.length > 0) {
        const dir = pendingDirs.shift();
        const dirLabel = dir ? " in folder '" + dir + "'" : '';
        let page = 1;
        let pageSize = 0;

        while (true) {
            if (pagesFetched >= MAX_PAGES) {
                truncated = true;
                reason = 'reached the ' + MAX_PAGES + ' page safety cap';
                break walk;
            }

            const result = await fetchPage(dir, page);
            if (result.error) {
                truncated = true;
                reason = 'request for page ' + page + dirLabel + ' failed: ' + result.error;
                break walk;
            }

            const data = result.data || {};
            if (data.errno && data.errno !== 0) {
                const apiError = data.errmsg || data.msg || ('Error ' + data.errno);
                if (pagesFetched === 0) {
                    return JSON.stringify({ error: apiError });
                }
                truncated = true;
                reason = 'API error on page ' + page + dirLabel + ': ' + apiError;
                break walk;
            }

            pagesFetched++;
            shareId = data.share_id || shareId;
            uk = data.uk || uk;

            const list = Array.isArray(data.list) ? data.list : [];
            let freshItems = 0;
            for (const item of list) {
                if (!item || typeof item !== 'object') continue;
                const key = String(
                    item.fs_id || ((item.path || '') + '|' + (item.server_filename || item.filename || ''))
                );
                if (seenItems.has(key)) continue;
                seenItems.add(key);
                freshItems++;
                if (item.isdir) {
                    const childDir = item.path || '';
                    if (childDir && !knownDirs.has(childDir)) {
                        knownDirs.add(childDir);
                        pendingDirs.push(childDir);
                    }
                }
            }
            if (freshItems > 0) {
                batches.push(data);
            }

            if (list.length === 0) break;
            // The server re-sent a page we already have, so paging cannot make
            // progress here. Stop this folder, but flag the listing as partial.
            if (freshItems === 0) {
                truncated = true;
                if (!reason) {
                    reason = 'page ' + page + dirLabel +
                        ' repeated items already seen (server ignored the page parameter)';
                }
                break;
            }

            if (page === 1) pageSize = list.length;
            const fullPage = pageSize > 0 && list.length >= pageSize;
            const rawHasMore = data.has_more;
            const hasMoreKnown = rawHasMore !== undefined && rawHasMore !== null;
            const hasMore = hasMoreKnown
                ? (rawHasMore !== false && rawHasMore !== 0 && rawHasMore !== '0')
                : false;
            // Trust `has_more` only to keep going; a partial page is the
            // reliable end-of-listing signal.
            if (!hasMore && !fullPage) break;

            const nextPage = Number(data.next_page);
            page = Number.isFinite(nextPage) && nextPage > page ? nextPage : page + 1;
            await sleep(DELAY_MS);
        }
    }

    return JSON.stringify({
        batches: batches,
        pages: pagesFetched,
        folders: knownDirs.size - 1,
        truncated: truncated,
        reason: reason
    });
})()
"""


def build_listing_script(terabox_url: str) -> str:
    """Build the in-page JS that walks every list page of a TeraBox share."""
    return (
        _LISTING_SCRIPT_TEMPLATE
        .replace("__TERABOX_URL__", json.dumps(terabox_url))
        .replace("__MAX_PAGES__", str(MAX_LIST_PAGES))
        .replace("__MAX_ATTEMPTS__", str(LIST_REQUEST_ATTEMPTS))
        .replace("__DELAY_MS__", str(LIST_REQUEST_DELAY_MS))
    )


def consume_listing_result(raw_result: Any, captured_files: List[FileInfo]) -> Optional[str]:
    """Parse the listing script output, extending captured_files with its batches.

    Returns an API error message if the site reported one, otherwise None. A
    warning is logged when the listing is known to be incomplete so a partial
    result is never mistaken for the full share.
    """
    if not isinstance(raw_result, str):
        return None
    try:
        result = json.loads(raw_result)
    except (json.JSONDecodeError, TypeError):
        logger.debug("Listing script returned a non-JSON payload")
        return None
    if not isinstance(result, dict):
        return None

    if result.get("error"):
        return str(result["error"])

    batches = result.get("batches") or []
    for batch in batches:
        try:
            captured_files.extend(parse_proxy_json(batch))
        except RuntimeError as err:
            return str(err)

    logger.debug(
        "Listed %s page(s) across %s nested folder(s)",
        result.get("pages", 0),
        result.get("folders", 0),
    )
    if result.get("truncated"):
        reason_str = str(result.get("reason") or "unknown reason")
        try:
            captured_files.truncated = True  # type: ignore[attr-defined]
            captured_files.reason = reason_str  # type: ignore[attr-defined]
            captured_files.truncation_reason = reason_str  # type: ignore[attr-defined]
        except AttributeError:
            pass
        logger.warning(
            "File listing is INCOMPLETE after %s page(s): %s. "
            "Some files are missing - re-run the command to fetch the rest.",
            result.get("pages", 0),
            reason_str,
        )
    return None


def dedupe_files(files: List[FileInfo]) -> ExtractedFileList:
    """Drop duplicate entries, preferring fs_id over download URL as identity."""
    truncated = getattr(files, "truncated", False) is True
    reason = getattr(files, "reason", None) or getattr(files, "truncation_reason", None)
    unique: Dict[str, FileInfo] = {}
    for f in files:
        if not f.download_url:
            continue
        key = f"fs:{f.fs_id}" if f.fs_id else f"url:{f.download_url}"
        if key not in unique:
            unique[key] = f
    return ExtractedFileList(unique.values(), truncated=truncated, reason=reason)


class TeraBoxAutomator:
    """Automates 1024teradl.com using headless/headed browser to extract download links."""

    def __init__(self, config: Optional[DownloadConfig] = None):
        self.config = config or DownloadConfig()
        self.last_listing_truncated: bool = False
        self.last_truncation_reason: Optional[str] = None

    def _finalize_extracted_files(self, files: List[FileInfo]) -> ExtractedFileList:
        """Record truncation metadata on the automator instance and return ExtractedFileList."""
        truncated = getattr(files, "truncated", False) is True
        reason = getattr(files, "reason", None) or getattr(files, "truncation_reason", None)
        self.last_listing_truncated = truncated
        self.last_truncation_reason = reason
        if isinstance(files, ExtractedFileList):
            return files
        return ExtractedFileList(files, truncated=truncated, reason=reason)

    async def extract_files(self, terabox_url: str) -> ExtractedFileList:
        """Navigate to 1024teradl.com, submit TeraBox share link, and extract files."""
        self.last_listing_truncated = False
        self.last_truncation_reason = None

        if not is_valid_terabox_url(terabox_url):
            raise ValueError(f"Invalid TeraBox shared link: {terabox_url}")

        # Checked up front so the "auto" fallback below cannot mistake a refusal
        # to run unsandboxed for an engine failure and retry the other engine.
        check_sandbox_policy(self.config.allow_no_sandbox)

        if self.config.browser_engine == "playwright":
            result = await self._extract_files_playwright(terabox_url)
            return self._finalize_extracted_files(result)
        elif self.config.browser_engine == "nodriver":
            result = await self._extract_files_nodriver(terabox_url)
            return self._finalize_extracted_files(result)
        else:
            # engine == "auto"
            try:
                result = await self._extract_files_nodriver(terabox_url)
                return self._finalize_extracted_files(result)
            except (ValueError, SandboxPolicyError):
                raise
            except Exception as nodriver_exc:
                logger.warning(f"nodriver failed ({nodriver_exc}), falling back to playwright...")
                try:
                    result = await self._extract_files_playwright(terabox_url)
                    return self._finalize_extracted_files(result)
                except CloudflareBlockError:
                    raise
                except Exception as pw_exc:
                    if is_cloudflare_error(nodriver_exc):
                        if isinstance(nodriver_exc, CloudflareBlockError):
                            raise nodriver_exc from pw_exc
                        raise CloudflareBlockError(str(nodriver_exc)) from pw_exc
                    if is_cloudflare_error(pw_exc):
                        raise CloudflareBlockError(str(pw_exc)) from pw_exc
                    raise

    async def _extract_files_nodriver(self, terabox_url: str) -> ExtractedFileList:
        """Extract files using nodriver (pure Python CDP automation)."""
        import nodriver as uc

        browser = None
        captured_files: ExtractedFileList = ExtractedFileList()
        api_error_message: Optional[str] = None
        pending_api_requests: Dict[str, str] = {}

        try:
            start_kwargs = {
                "headless": self.config.headless,
                "browser_args": [
                    "--window-size=1920,1080",
                    "--disable-blink-features=AutomationControlled",
                ],
            }
            if self.config.chrome_executable_path and os.path.exists(
                self.config.chrome_executable_path
            ):
                start_kwargs["browser_executable_path"] = self.config.chrome_executable_path
            if should_disable_sandbox(self.config.allow_no_sandbox):
                start_kwargs["sandbox"] = False

            browser = await uc.start(**start_kwargs)
            page = await browser.get("https://1024teradl.com/")

            # Wait for Cloudflare Turnstile challenge if present
            challenge_resolved = False
            challenge_repeated = False
            last_title: Optional[str] = None
            had_challenge_iframe = False
            challenge_clicks = 0
            last_click_attempt: Optional[int] = None
            attempts = max(1, int(self.config.challenge_timeout_attempts))
            poll_interval = max(0.0, float(self.config.challenge_poll_interval))
            max_clicks = max(1, int(self.config.challenge_max_clicks))
            cooldown_attempts = max(1, int(self.config.challenge_click_cooldown_attempts))

            for attempt_idx in range(attempts):
                await asyncio.sleep(poll_interval)
                try:
                    title_val = await page.evaluate("document.title")
                    last_title = title_val if isinstance(title_val, str) else None
                except Exception:
                    last_title = None

                if last_title and not is_cloudflare_title(last_title):
                    challenge_resolved = True
                    break

                try:
                    iframe = await page.select(
                        "iframe[src*='cloudflare'], iframe[src*='turnstile'], iframe[title*='Cloudflare'], iframe[title*='challenge']"
                    )
                    if iframe:
                        had_challenge_iframe = True
                        if (
                            challenge_clicks >= max_clicks
                            and last_click_attempt is not None
                            and (attempt_idx - last_click_attempt) >= cooldown_attempts
                        ):
                            challenge_repeated = True
                            break
                        if challenge_clicks < max_clicks and (
                            last_click_attempt is None
                            or (attempt_idx - last_click_attempt) >= cooldown_attempts
                        ):
                            await iframe.mouse_click()
                            challenge_clicks += 1
                            last_click_attempt = attempt_idx
                except Exception:
                    pass

            if not challenge_resolved:
                if challenge_repeated:
                    raise CloudflareBlockError(
                        f"Blocked by Cloudflare bot detection: Turnstile challenge repeated after {challenge_clicks} click attempts (title={last_title!r})."
                    )
                html_after_wait: Optional[str] = None
                try:
                    raw_html = await page.get_content()
                    if isinstance(raw_html, str):
                        html_after_wait = raw_html
                except Exception:
                    html_after_wait = None

                if is_cloudflare_challenge_or_block(
                    last_title,
                    html_after_wait,
                    has_input=False,
                    has_challenge_element=had_challenge_iframe,
                ):
                    raise CloudflareBlockError(
                        f"Blocked by Cloudflare bot detection or unresolved Turnstile challenge (title={last_title!r})."
                    )

            # Enable CDP network domain and block ad networks
            await page.send(uc.cdp.network.enable())
            try:
                await page.send(uc.cdp.network.set_blocked_ur_ls(urls=AD_BLOCKED_PATTERNS))
            except Exception:
                pass

            async def on_response(event: uc.cdp.network.ResponseReceived):
                url = event.response.url
                if "proxy" in url or "teradl.com/api" in url:
                    pending_api_requests[event.request_id] = url

            async def on_loading_finished(event: uc.cdp.network.LoadingFinished):
                nonlocal api_error_message
                if event.request_id in pending_api_requests:
                    url = pending_api_requests.pop(event.request_id)
                    logger.debug("Intercepted finished TeraBox API call: %s", url)
                    try:
                        res = await page.send(
                            uc.cdp.network.get_response_body(event.request_id)
                        )
                        body_text = res[0]
                        if body_text:
                            data = json.loads(body_text)
                            try:
                                parsed = parse_proxy_json(data)
                                captured_files.extend(parsed)
                            except RuntimeError as err:
                                api_error_message = str(err)
                                logger.warning("API reported error: %s", api_error_message)
                    except Exception as e:
                        logger.debug("Failed to read API response body: %s", e)

            page.add_handler(uc.cdp.network.ResponseReceived, on_response)
            page.add_handler(uc.cdp.network.LoadingFinished, on_loading_finished)

            # Direct In-Page Multi-Page Extraction (100% immune to UI ad blockers, overlays, modals, and popunders)
            try:
                fetch_res_raw = await page.evaluate(
                    build_listing_script(terabox_url), await_promise=True
                )
                api_error_message = (
                    consume_listing_result(fetch_res_raw, captured_files) or api_error_message
                )
            except Exception as e:
                logger.debug("Direct in-page fetch fallback: %s", e)

            # If not captured via direct fetch, fall back to UI form submission
            if not captured_files and not api_error_message:
                # Block popunder/popup ads and clean ad overlay backdrops
                cleanup_script = """
                (() => {
                    window.open = function() { return null; };
                    const adSelectors = [
                        'ins.adsbygoogle',
                        '[id*="google_ads"]',
                        '[id*="aswift"]',
                        'iframe[src*="ad"]',
                        'iframe[id*="ad"]',
                        '[class*="ad-"]',
                        '[class*="ads-"]',
                        '[class*="ad_"]',
                        '[id*="pop"]',
                        '[class*="popup"]',
                        '[class*="overlay"]',
                        '[class*="modal"]',
                        '.ad-container',
                        '.ads-wrapper',
                    ];
                    for (const sel of adSelectors) {
                        document.querySelectorAll(sel).forEach(el => {
                            if (!el.querySelector('input') && !el.querySelector('iframe[src*="turnstile"]') && !el.querySelector('iframe[src*="cloudflare"]')) {
                                el.remove();
                            }
                        });
                    }
                    document.querySelectorAll('div, section, aside, span, a').forEach(el => {
                        const style = window.getComputedStyle(el);
                        if ((style.position === 'fixed' || style.position === 'absolute') && parseInt(style.zIndex, 10) >= 100) {
                            if (!el.querySelector('input') && !el.querySelector('iframe[src*="turnstile"]') && !el.querySelector('iframe[src*="cloudflare"]') && !el.querySelector('button[type="submit"]')) {
                                el.remove();
                            }
                        }
                    });
                })()
                """
                try:
                    await page.evaluate(cleanup_script)
                except Exception:
                    pass

                # Fill input and submit using robust JavaScript event dispatching
                submit_script = f"""
                (() => {{
                    const targetUrl = {json.dumps(terabox_url)};
                    const inp = document.querySelector('input[placeholder*="Terabox"]') ||
                              document.querySelector('input[type="text"]') ||
                              document.querySelector('input[type="url"]') ||
                              document.querySelector('input:not([type="hidden"])');
                    if (inp) {{
                        const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
                        nativeSetter.call(inp, targetUrl);
                        inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    }}
                    const btn = document.querySelector('button[type="submit"]') ||
                                document.querySelector('form button') ||
                                document.querySelector('button');
                    if (btn) {{
                        btn.click();
                    }}
                    const form = document.querySelector('form');
                    if (form) {{
                        form.dispatchEvent(new Event('submit', {{ bubbles: true, cancelable: true }}));
                    }}
                }})()
                """
                try:
                    await page.evaluate(submit_script)
                except Exception:
                    pass

                # Fallback direct element click
                try:
                    btn = await page.select("button[type='submit']")
                    if btn:
                        await btn.mouse_click()
                except Exception:
                    pass

                # Wait for initial response or DOM update
                for _ in range(15):
                    await asyncio.sleep(poll_interval)
                    if captured_files or api_error_message:
                        break

            if api_error_message:
                if is_cloudflare_error(RuntimeError(api_error_message)) or is_cloudflare_html(
                    api_error_message
                ):
                    raise CloudflareBlockError(api_error_message)
                raise RuntimeError(api_error_message)

            if not captured_files:
                html = await page.get_content()
                html_str = html if isinstance(html, str) else ""
                dom_files = parse_dom_files(html_str)
                captured_files.extend(dom_files)
                if not captured_files and is_cloudflare_challenge_or_block(
                    last_title,
                    html_str,
                    has_input=False,
                    has_challenge_element=had_challenge_iframe,
                ):
                    raise CloudflareBlockError(
                        f"Blocked by Cloudflare bot detection or unresolved Turnstile challenge (title={last_title!r})."
                    )

            return self._finalize_extracted_files(dedupe_files(captured_files))

        finally:
            if browser:
                try:
                    if hasattr(browser, "aclose"):
                        await browser.aclose()
                    browser.stop()
                except Exception:
                    pass

    async def _extract_files_playwright(self, terabox_url: str) -> ExtractedFileList:
        """Extract files using Playwright with stealth (if available in environment)."""
        try:
            from playwright.async_api import async_playwright
            try:
                from playwright_stealth import Stealth

                async def _apply_stealth(page):
                    try:
                        # Disable overrides that spoof a mismatched OS/GPU ("Win32" /
                        # "Intel Iris") in Window while leaving Turnstile WebWorkers
                        # with the native OS/GPU, which triggers repeating Turnstile loops.
                        stealth_cfg = Stealth(
                            navigator_platform=False,
                            navigator_platform_override=None,
                            webgl_vendor=False,
                            navigator_plugins=False,
                            iframe_content_window=False,
                            error_prototype=False,
                            navigator_hardware_concurrency=False,
                        )
                    except TypeError:
                        stealth_cfg = Stealth()
                    await stealth_cfg.apply_stealth_async(page)
            except ImportError:
                from playwright_stealth import stealth_async as _apply_stealth
        except ImportError as exc:
            raise RuntimeError(
                "Playwright or playwright-stealth is not installed or available."
            ) from exc

        captured_files: ExtractedFileList = ExtractedFileList()
        api_error_message: Optional[str] = None

        async with async_playwright() as p:
            args = [
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1920,1080",
            ]
            if should_disable_sandbox(self.config.allow_no_sandbox):
                args.append("--no-sandbox")

            launch_kwargs: Dict[str, Any] = {
                "headless": self.config.headless,
                "args": args,
                "ignore_default_args": ["--enable-automation"],
            }
            if self.config.chrome_executable_path and os.path.exists(
                self.config.chrome_executable_path
            ):
                launch_kwargs["executable_path"] = self.config.chrome_executable_path
            else:
                try:
                    launch_kwargs["channel"] = "chrome"
                except Exception:
                    pass

            try:
                browser = await p.chromium.launch(**launch_kwargs)
            except Exception:
                launch_kwargs.pop("channel", None)
                browser = await p.chromium.launch(**launch_kwargs)

            try:
                context_kwargs: Dict[str, Any] = (
                    {"viewport": {"width": 1920, "height": 1080}}
                    if self.config.headless
                    else {"no_viewport": True}
                )
                context = await browser.new_context(**context_kwargs)
                page = await context.new_page()
                await _apply_stealth(page)

                async def handle_response(response):
                    nonlocal api_error_message
                    if "proxy" in response.url or "teradl.com/api" in response.url:
                        try:
                            text = await response.text()
                            if text:
                                data = json.loads(text)
                                try:
                                    parsed = parse_proxy_json(data)
                                    captured_files.extend(parsed)
                                except RuntimeError as err:
                                    api_error_message = str(err)
                        except Exception:
                            pass

                page.on("response", handle_response)
                await page.goto("https://1024teradl.com/", wait_until="domcontentloaded", timeout=60000)

                # Wait for Cloudflare Turnstile challenge to resolve and input box to appear
                challenge_resolved = False
                challenge_repeated = False
                last_title: Optional[str] = None
                had_challenge_element = False
                challenge_clicks = 0
                last_click_attempt: Optional[int] = None
                attempts = max(1, int(self.config.challenge_timeout_attempts))
                poll_interval = max(0.0, float(self.config.challenge_poll_interval))
                max_clicks = max(1, int(self.config.challenge_max_clicks))
                cooldown_attempts = max(1, int(self.config.challenge_click_cooldown_attempts))

                for attempt_idx in range(attempts):
                    try:
                        title_val = await page.title()
                        last_title = title_val if isinstance(title_val, str) else None
                    except Exception:
                        last_title = None

                    if last_title and not is_cloudflare_title(last_title):
                        try:
                            inp = await page.wait_for_selector(
                                "input[placeholder*='Terabox'], input[placeholder*='terabox'], input[type='url'], input[type='text']",
                                timeout=1000,
                            )
                            if inp:
                                challenge_resolved = True
                                break
                        except Exception:
                            pass

                    target_box: Optional[Dict[str, float]] = None
                    found_challenge_this_poll = False
                    try:
                        for iframe_sel in [
                            "iframe[src*='cloudflare']",
                            "iframe[src*='turnstile']",
                            "iframe[title*='Cloudflare']",
                            "iframe[title*='challenge']",
                            "#turnstile-wrapper",
                            ".cf-turnstile",
                            "#cf-turnstile",
                            "#challenge-stage",
                        ]:
                            el = await page.query_selector(iframe_sel)
                            if el:
                                had_challenge_element = True
                                found_challenge_this_poll = True
                                box = await el.bounding_box()
                                if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
                                    target_box = box
                                break
                    except Exception:
                        pass

                    cf_frames = []
                    for frame in getattr(page, "frames", []) or []:
                        frame_url = getattr(frame, "url", "") or ""
                        if "cloudflare" in frame_url or "turnstile" in frame_url:
                            had_challenge_element = True
                            found_challenge_this_poll = True
                            cf_frames.append(frame)
                            if target_box is None and hasattr(frame, "frame_element"):
                                try:
                                    frame_el = await frame.frame_element()
                                    if frame_el and hasattr(frame_el, "bounding_box"):
                                        box = await frame_el.bounding_box()
                                        if (
                                            isinstance(box, dict)
                                            and box.get("width", 0) > 0
                                            and box.get("height", 0) > 0
                                        ):
                                            target_box = box
                                except Exception:
                                    pass

                    if found_challenge_this_poll:
                        if (
                            challenge_clicks >= max_clicks
                            and last_click_attempt is not None
                            and (attempt_idx - last_click_attempt) >= cooldown_attempts
                        ):
                            challenge_repeated = True
                            break

                        if challenge_clicks < max_clicks and (
                            last_click_attempt is None
                            or (attempt_idx - last_click_attempt) >= cooldown_attempts
                        ):
                            clicked_this_poll = False
                            if target_box is not None:
                                try:
                                    width = float(target_box["width"])
                                    height = float(target_box["height"])
                                    offset_x = min(28.0, width / 2.0) if width >= 60.0 else width / 2.0
                                    click_x = float(target_box["x"]) + offset_x
                                    click_y = float(target_box["y"]) + height / 2.0
                                    if hasattr(page, "mouse") and hasattr(page.mouse, "move"):
                                        await page.mouse.move(click_x, click_y, steps=5)
                                    await page.mouse.click(click_x, click_y)
                                    clicked_this_poll = True
                                except Exception:
                                    pass

                            if not clicked_this_poll:
                                for frame in cf_frames:
                                    try:
                                        checkbox = await frame.wait_for_selector(
                                            "input[type='checkbox'], .cb-lb, #challenge-stage",
                                            timeout=500,
                                        )
                                        if checkbox:
                                            await checkbox.click()
                                            clicked_this_poll = True
                                            break
                                    except Exception:
                                        pass

                            if clicked_this_poll:
                                challenge_clicks += 1
                                last_click_attempt = attempt_idx

                    await asyncio.sleep(poll_interval)

                if not challenge_resolved:
                    if challenge_repeated:
                        raise CloudflareBlockError(
                            f"Blocked by Cloudflare bot detection: Turnstile challenge repeated after {challenge_clicks} click attempts (title={last_title!r})."
                        )
                    html_after_wait: Optional[str] = None
                    try:
                        raw_html = await page.content()
                        if isinstance(raw_html, str):
                            html_after_wait = raw_html
                    except Exception:
                        html_after_wait = None

                    if is_cloudflare_challenge_or_block(
                        last_title,
                        html_after_wait,
                        has_input=False,
                        has_challenge_element=had_challenge_element,
                    ):
                        raise CloudflareBlockError(
                            f"Blocked by Cloudflare bot detection or unresolved Turnstile challenge (title={last_title!r})."
                        )

                # Direct In-Page Multi-Page Extraction (100% immune to UI ad blockers, overlays, modals, and popunders)
                try:
                    fetch_res_raw = await page.evaluate(build_listing_script(terabox_url))
                    api_error_message = (
                        consume_listing_result(fetch_res_raw, captured_files) or api_error_message
                    )
                except Exception as e:
                    logger.debug("Playwright in-page fetch fallback: %s", e)

                # If not captured via direct fetch, fall back to UI form submission
                if not captured_files and not api_error_message:
                    # Block popunder/popup ads and clean ad overlay backdrops
                    cleanup_script = """
                    (() => {
                        window.open = function() { return null; };
                        const adSelectors = [
                            'ins.adsbygoogle',
                            '[id*="google_ads"]',
                            '[id*="aswift"]',
                            'iframe[src*="ad"]',
                            'iframe[id*="ad"]',
                            '[class*="ad-"]',
                            '[class*="ads-"]',
                            '[class*="ad_"]',
                            '[id*="pop"]',
                            '[class*="popup"]',
                            '[class*="overlay"]',
                            '[class*="modal"]',
                            '.ad-container',
                            '.ads-wrapper',
                        ];
                        for (const sel of adSelectors) {
                            document.querySelectorAll(sel).forEach(el => {
                                if (!el.querySelector('input') && !el.querySelector('iframe[src*="turnstile"]') && !el.querySelector('iframe[src*="cloudflare"]')) {
                                    el.remove();
                                }
                            });
                        }
                        document.querySelectorAll('div, section, aside, span, a').forEach(el => {
                            const style = window.getComputedStyle(el);
                            if ((style.position === 'fixed' || style.position === 'absolute') && parseInt(style.zIndex, 10) >= 100) {
                                if (!el.querySelector('input') && !el.querySelector('iframe[src*="turnstile"]') && !el.querySelector('iframe[src*="cloudflare"]') && !el.querySelector('button[type="submit"]')) {
                                    el.remove();
                                }
                            }
                        });
                    })()
                    """
                    try:
                        await page.evaluate(cleanup_script)
                    except Exception:
                        pass

                    # Fill input and submit using robust JavaScript event dispatching
                    submit_script = f"""
                    (() => {{
                        const targetUrl = {json.dumps(terabox_url)};
                        const inp = document.querySelector('input[placeholder*="Terabox"]') ||
                                  document.querySelector('input[type="text"]') ||
                                  document.querySelector('input[type="url"]') ||
                                  document.querySelector('input:not([type="hidden"])');
                        if (inp) {{
                            const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
                            nativeSetter.call(inp, targetUrl);
                            inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        }}
                        const btn = document.querySelector('button[type="submit"]') ||
                                    document.querySelector('form button') ||
                                    document.querySelector('button');
                        if (btn) {{
                            btn.click();
                        }}
                        const form = document.querySelector('form');
                        if (form) {{
                            form.dispatchEvent(new Event('submit', {{ bubbles: true, cancelable: true }}));
                        }}
                    }})()
                    """
                    try:
                        await page.evaluate(submit_script)
                    except Exception:
                        pass

                    try:
                        await page.fill("input[placeholder*='Terabox']", terabox_url)
                        await page.click("button[type='submit']")
                    except Exception:
                        pass

                    # Wait for initial response or DOM update
                    for _ in range(15):
                        await asyncio.sleep(poll_interval)
                        if captured_files or api_error_message:
                            break

                if api_error_message:
                    if is_cloudflare_error(RuntimeError(api_error_message)) or is_cloudflare_html(
                        api_error_message
                    ):
                        raise CloudflareBlockError(api_error_message)
                    raise RuntimeError(api_error_message)

                if not captured_files:
                    html = await page.content()
                    html_str = html if isinstance(html, str) else ""
                    captured_files.extend(parse_dom_files(html_str))
                    if not captured_files and is_cloudflare_challenge_or_block(
                        last_title,
                        html_str,
                        has_input=False,
                        has_challenge_element=had_challenge_element,
                    ):
                        raise CloudflareBlockError(
                            f"Blocked by Cloudflare bot detection or unresolved Turnstile challenge (title={last_title!r})."
                        )
            finally:
                await browser.close()

        return self._finalize_extracted_files(dedupe_files(captured_files))

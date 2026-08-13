"""Browser automation for 1024teradl.com using nodriver / playwright."""

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from terabox_dl.models import DownloadConfig, FileInfo
from terabox_dl.utils import is_valid_terabox_url, parse_size, sanitize_filename

logger = logging.getLogger(__name__)

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


class TeraBoxAutomator:
    """Automates 1024teradl.com using headless/headed browser to extract download links."""

    def __init__(self, config: Optional[DownloadConfig] = None):
        self.config = config or DownloadConfig()

    async def extract_files(self, terabox_url: str) -> List[FileInfo]:
        """Navigate to 1024teradl.com, submit TeraBox share link, and extract files."""
        if not is_valid_terabox_url(terabox_url):
            raise ValueError(f"Invalid TeraBox shared link: {terabox_url}")

        if self.config.browser_engine == "playwright":
            return await self._extract_files_playwright(terabox_url)
        elif self.config.browser_engine == "nodriver":
            return await self._extract_files_nodriver(terabox_url)
        else:
            # engine == "auto"
            try:
                return await self._extract_files_nodriver(terabox_url)
            except Exception as e:
                logger.warning(f"nodriver failed ({e}), falling back to playwright...")
                return await self._extract_files_playwright(terabox_url)

    async def _extract_files_nodriver(self, terabox_url: str) -> List[FileInfo]:
        """Extract files using nodriver (pure Python CDP automation)."""
        import nodriver as uc

        browser = None
        captured_files: List[FileInfo] = []
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
            if self.config.chrome_executable_path:
                start_kwargs["browser_executable_path"] = self.config.chrome_executable_path
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                start_kwargs["sandbox"] = False

            browser = await uc.start(**start_kwargs)
            page = await browser.get("https://1024teradl.com/")

            # Wait for Cloudflare Turnstile challenge if present
            for _ in range(30):
                await asyncio.sleep(1)
                title = await page.evaluate("document.title")
                if title and "Just a moment" not in title and "Cloudflare" not in title:
                    break
                try:
                    iframe = await page.select(
                        "iframe[src*='cloudflare'], iframe[src*='turnstile'], iframe[title*='Cloudflare'], iframe[title*='challenge']"
                    )
                    if iframe:
                        await iframe.mouse_click()
                except Exception:
                    pass

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
            direct_fetch_script = f"""
            (async () => {{
                const teraboxUrl = {json.dumps(terabox_url)};
                const allBatches = [];
                let curPage = 1;
                let hasMore = true;
                let shareId = null;
                let uk = null;
                
                while (hasMore && curPage <= 50) {{
                    const payload = {{ url: teraboxUrl, page: curPage }};
                    if (shareId && uk) {{
                        payload.share_id = shareId;
                        payload.uk = uk;
                        payload.dir = '';
                    }}
                    
                    try {{
                        const r = await fetch('/api/proxy', {{
                            method: 'POST',
                            headers: {{ 'Content-Type': 'application/json' }},
                            body: JSON.stringify(payload)
                        }});
                        if (!r.ok) break;
                        const data = await r.json();
                        if (data.errno && data.errno !== 0) {{
                            return JSON.stringify({{ error: data.errmsg || data.msg || ('Error ' + data.errno) }});
                        }}
                        allBatches.push(data);
                        shareId = data.share_id || shareId;
                        uk = data.uk || uk;
                        hasMore = Boolean(data.has_more);
                        curPage = data.next_page || (curPage + 1);
                        if (!data.list || data.list.length === 0) break;
                    }} catch(e) {{
                        break;
                    }}
                }}
                return JSON.stringify({{ batches: allBatches }});
            }})()
            """
            try:
                fetch_res_raw = await page.evaluate(direct_fetch_script, await_promise=True)
                if isinstance(fetch_res_raw, str):
                    fetch_res = json.loads(fetch_res_raw)
                    if "error" in fetch_res:
                        api_error_message = fetch_res["error"]
                    elif "batches" in fetch_res:
                        for b in fetch_res["batches"]:
                            parsed = parse_proxy_json(b)
                            captured_files.extend(parsed)
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
                    await asyncio.sleep(1)
                    if captured_files or api_error_message:
                        break

            if api_error_message:
                raise RuntimeError(api_error_message)

            if not captured_files:
                html = await page.get_content()
                dom_files = parse_dom_files(html)
                captured_files.extend(dom_files)

            # Deduplicate by download_url
            unique_files: Dict[str, FileInfo] = {}
            for f in captured_files:
                if f.download_url and f.download_url not in unique_files:
                    unique_files[f.download_url] = f

            return list(unique_files.values())

        finally:
            if browser:
                try:
                    if hasattr(browser, "aclose"):
                        await browser.aclose()
                    browser.stop()
                except Exception:
                    pass

    async def _extract_files_playwright(self, terabox_url: str) -> List[FileInfo]:
        """Extract files using Playwright with stealth (if available in environment)."""
        try:
            from playwright.async_api import async_playwright
            try:
                from playwright_stealth import Stealth
                async def _apply_stealth(page):
                    await Stealth().apply_stealth_async(page)
            except ImportError:
                from playwright_stealth import stealth_async as _apply_stealth
        except ImportError as exc:
            raise RuntimeError(
                "Playwright or playwright-stealth is not installed or available."
            ) from exc

        captured_files: List[FileInfo] = []
        api_error_message: Optional[str] = None

        async with async_playwright() as p:
            args = [
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1920,1080",
            ]
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                args.append("--no-sandbox")

            launch_kwargs = {
                "headless": self.config.headless,
                "args": args,
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

            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
            )
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
            for _ in range(30):
                title = await page.title()
                if title and "Just a moment" not in title and "Cloudflare" not in title:
                    try:
                        inp = await page.wait_for_selector(
                            "input[placeholder*='Terabox'], input", timeout=1000
                        )
                        if inp:
                            break
                    except Exception:
                        pass
                try:
                    for iframe_sel in [
                        "iframe[src*='cloudflare']",
                        "iframe[src*='turnstile']",
                        "iframe[title*='Cloudflare']",
                        "iframe[title*='challenge']",
                    ]:
                        el = await page.query_selector(iframe_sel)
                        if el:
                            box = await el.bounding_box()
                            if box and box["width"] > 0 and box["height"] > 0:
                                await page.mouse.click(
                                    box["x"] + box["width"] / 2,
                                    box["y"] + box["height"] / 2,
                                )
                            break
                except Exception:
                    pass
                for frame in page.frames:
                    if "cloudflare" in frame.url or "turnstile" in frame.url:
                        try:
                            checkbox = await frame.wait_for_selector(
                                "input[type='checkbox'], .cb-lb, #challenge-stage",
                                timeout=500,
                            )
                            if checkbox:
                                await checkbox.click()
                        except Exception:
                            pass
                await asyncio.sleep(1)

            # Direct In-Page Multi-Page Extraction (100% immune to UI ad blockers, overlays, modals, and popunders)
            direct_fetch_script = f"""
            (async () => {{
                const teraboxUrl = {json.dumps(terabox_url)};
                const allBatches = [];
                let curPage = 1;
                let hasMore = true;
                let shareId = null;
                let uk = null;
                
                while (hasMore && curPage <= 50) {{
                    const payload = {{ url: teraboxUrl, page: curPage }};
                    if (shareId && uk) {{
                        payload.share_id = shareId;
                        payload.uk = uk;
                        payload.dir = '';
                    }}
                    
                    try {{
                        const r = await fetch('/api/proxy', {{
                            method: 'POST',
                            headers: {{ 'Content-Type': 'application/json' }},
                            body: JSON.stringify(payload)
                        }});
                        if (!r.ok) break;
                        const data = await r.json();
                        if (data.errno && data.errno !== 0) {{
                            return JSON.stringify({{ error: data.errmsg || data.msg || ('Error ' + data.errno) }});
                        }}
                        allBatches.push(data);
                        shareId = data.share_id || shareId;
                        uk = data.uk || uk;
                        hasMore = Boolean(data.has_more);
                        curPage = data.next_page || (curPage + 1);
                        if (!data.list || data.list.length === 0) break;
                    }} catch(e) {{
                        break;
                    }}
                }}
                return JSON.stringify({{ batches: allBatches }});
            }})()
            """
            try:
                fetch_res_raw = await page.evaluate(direct_fetch_script)
                if isinstance(fetch_res_raw, str):
                    fetch_res = json.loads(fetch_res_raw)
                    if "error" in fetch_res:
                        api_error_message = fetch_res["error"]
                    elif "batches" in fetch_res:
                        for b in fetch_res["batches"]:
                            parsed = parse_proxy_json(b)
                            captured_files.extend(parsed)
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
                    await asyncio.sleep(1)
                    if captured_files or api_error_message:
                        break

            if api_error_message:
                raise RuntimeError(api_error_message)

            if not captured_files:
                html = await page.content()
                captured_files.extend(parse_dom_files(html))

            await browser.close()

        unique_files: Dict[str, FileInfo] = {}
        for f in captured_files:
            if f.download_url and f.download_url not in unique_files:
                unique_files[f.download_url] = f
        return list(unique_files.values())

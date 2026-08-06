"""Utility functions for filename sanitization, size parsing, and URL validation."""

import re
from urllib.parse import urlparse

# Supported official TeraBox domains and common mirror sites
SUPPORTED_DOMAINS = {
    "terabox.com",
    "www.terabox.com",
    "1024terabox.com",
    "www.1024terabox.com",
    "1024tera.com",
    "www.1024tera.com",
    "teraboxapp.com",
    "www.teraboxapp.com",
    "freeterabox.com",
    "www.freeterabox.com",
    "terabox.app",
    "www.terabox.app",
    "teraboxlink.com",
    "www.teraboxlink.com",
    "terafileshare.com",
    "www.terafileshare.com",
    "neoxb.com",
    "www.neoxb.com",
}


def is_valid_terabox_url(url: str) -> bool:
    """Check whether the provided URL is a valid TeraBox shared link."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
        if parsed.scheme not in ("http", "https"):
            return False
        domain = parsed.netloc.lower()
        # Handle subdomains or exact matches
        if any(domain == d or domain.endswith("." + d) for d in SUPPORTED_DOMAINS):
            # Typical shared paths contain '/s/' or are valid share links
            if "/s/" in parsed.path or "share" in parsed.path or len(parsed.path) > 1:
                return True
        return False
    except Exception:
        return False


def sanitize_filename(name: str) -> str:
    """Sanitize a filename for safe writing across operating systems."""
    if not name:
        return "unnamed_terabox_file"
    # Replace dangerous characters
    cleaned = re.sub(r'[\\/*?:"<>|]', "_", name)
    # Remove control characters
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", cleaned)
    # Remove leading/trailing spaces and dots
    cleaned = cleaned.strip(" .")
    return cleaned or "unnamed_terabox_file"


def parse_size(size_str: str) -> int:
    """Parse a human-readable size string (e.g., '1.5 GB', '250 MB') into bytes."""
    if not size_str:
        return 0
    s = size_str.strip().upper()
    match = re.match(r"^([\d\.]+)\s*(B|BYTES?|KB|MB|GB|TB)?$", s)
    if not match:
        return 0
    val, unit = match.groups()
    try:
        val_float = float(val)
    except ValueError:
        return 0

    unit_multipliers = {
        None: 1,
        "B": 1,
        "BYTE": 1,
        "BYTES": 1,
        "KB": 1024,
        "MB": 1024**2,
        "GB": 1024**3,
        "TB": 1024**4,
    }
    multiplier = unit_multipliers.get(unit, 1)
    return int(val_float * multiplier)


def format_bytes(size: int) -> str:
    """Format an integer byte count into human-readable string representation."""
    if size <= 0:
        return "0 B"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    unit_idx = 0
    val = float(size)
    while val >= 1024.0 and unit_idx < len(units) - 1:
        val /= 1024.0
        unit_idx += 1
    return f"{val:.2f} {units[unit_idx]}"

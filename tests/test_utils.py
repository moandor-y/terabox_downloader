"""Unit tests for utils.py."""

import pytest
from terabox_dl.utils import (
    format_bytes,
    is_valid_terabox_url,
    parse_size,
    sanitize_filename,
)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://terabox.com/s/1A2b3C4d5E6f7G8h9I0jKlM", True),
        ("https://www.1024tera.com/s/1xyz123", True),
        ("https://teraboxapp.com/s/1abc-def", True),
        ("https://freeterabox.com/s/199999", True),
        ("https://google.com/s/1abc", False),
        ("not_a_url", False),
        ("", False),
    ],
)
def test_is_valid_terabox_url(url, expected):
    assert is_valid_terabox_url(url) == expected


@pytest.mark.parametrize(
    "name,expected",
    [
        ("test/file*name?.mp4", "test_file_name_.mp4"),
        ("normal_file.zip", "normal_file.zip"),
        ("   .hidden_file.txt...   ", "hidden_file.txt"),
        ("", "unnamed_terabox_file"),
    ],
)
def test_sanitize_filename(name, expected):
    assert sanitize_filename(name) == expected


@pytest.mark.parametrize(
    "size_str,expected",
    [
        ("500 B", 500),
        ("1 KB", 1024),
        ("1.5 MB", int(1.5 * 1024**2)),
        ("2 GB", 2 * 1024**3),
        ("0", 0),
        ("invalid", 0),
    ],
)
def test_parse_size(size_str, expected):
    assert parse_size(size_str) == expected


@pytest.mark.parametrize(
    "size,expected",
    [
        (0, "0 B"),
        (500, "500.00 B"),
        (1024, "1.00 KiB"),
        (1024 * 1024 * 5, "5.00 MiB"),
        (1024 * 1024 * 1024 * 2, "2.00 GiB"),
    ],
)
def test_format_bytes(size, expected):
    assert format_bytes(size) == expected

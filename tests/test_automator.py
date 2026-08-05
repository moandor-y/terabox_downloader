"""Unit tests for automator.py."""

import pytest
from terabox_dl.automator import TeraBoxAutomator, parse_dom_files, parse_proxy_json


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

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest

from groupmate.adapters import sticker_media
from groupmate.adapters.sticker_media import load_sticker_files
from tests.social_runtime.stickers.test_lexicon import gif_bytes


def test_local_path_is_aliased_onto_url_without_download(tmp_path, monkeypatch):
    content = gif_bytes()
    path = tmp_path / "one.gif"
    path.write_bytes(content)
    url = "https://gchat.qpic.cn/one.gif"
    payload = {
        "media": [{"type": "image", "file": str(path), "url": url}],
        "segments": [{"type": "image", "data": {"file": str(path), "url": url}}],
    }

    def fail_fetch(*_args, **_kwargs):
        raise AssertionError("local bytes should not trigger a remote fetch")

    monkeypatch.setattr(sticker_media, "fetch_sticker_bytes", fail_fetch)
    files = load_sticker_files(payload)
    assert files[str(path)] == content
    assert files[url] == content


def test_loopback_url_is_downloaded_when_file_is_only_a_name():
    content = gif_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "image/gif")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/one.gif"
    try:
        files = load_sticker_files(
            {
                "media": [{"type": "image", "file": "one.gif", "url": url}],
                "segments": [
                    {"type": "image", "data": {"file": "one.gif", "url": url}}
                ],
            }
        )
    finally:
        server.shutdown()
        server.server_close()
    assert files[url] == content
    assert files["one.gif"] == content


def test_private_lan_url_is_rejected(monkeypatch):
    monkeypatch.setattr(
        sticker_media.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(0, 0, 0, "", ("10.0.0.8", 80))],
    )
    with pytest.raises(ValueError, match="not allowed"):
        sticker_media.fetch_sticker_bytes("http://internal.example/x.gif")

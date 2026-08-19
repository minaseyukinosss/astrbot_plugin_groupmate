from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).parents[2]
INDEX = ROOT / "pages" / "settings" / "index.html"


class FakeAstrBotHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if self.path.split("?", 1)[0] == "/fake/settings/index.html":
            source = INDEX.read_text(encoding="utf-8")
            source = source.replace(
                'href="./styles/',
                'href="/pages/settings/styles/',
            ).replace(
                '<script type="module" src="./app.js"></script>',
                '<script src="/tests/page/fixtures/fake_bridge.js?gate-d=2"></script>\n'
                '    <script type="module" src="/pages/settings/app.js"></script>',
                1,
            )
            payload = source.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        super().do_GET()

    def log_message(self, _format, *_args):
        return


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), FakeAstrBotHandler)
    print(f"fake AstrBot page: http://127.0.0.1:{args.port}/fake/settings/index.html", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

"""Loopback-only HTTP surface for explicit human evaluation review."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from .review_cli import ReviewSession


_MAX_REQUEST_BYTES = 64 * 1024
_STATIC_ROOT = Path(__file__).with_name("review_page")
_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/review.css": ("review.css", "text/css; charset=utf-8"),
    "/review.mjs": ("review.mjs", "text/javascript; charset=utf-8"),
    "/review_state.mjs": ("review_state.mjs", "text/javascript; charset=utf-8"),
}


def _is_loopback(host: str) -> bool:
    normalized = str(host or "").strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def _handler_for(session: ReviewSession, static_root: Path):
    class ReviewRequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:
            return

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/api/current":
                self._send_json(
                    HTTPStatus.OK,
                    {"item": session.next_pending(), "progress": session.progress()},
                )
                return
            static_file = _STATIC_FILES.get(path)
            if static_file is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            name, content_type = static_file
            source = static_root / name
            try:
                body = source.read_bytes()
            except FileNotFoundError:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": "review page is unavailable"},
                )
                return
            self._send(HTTPStatus.OK, body, content_type)

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            if path not in {"/api/approve", "/api/correct", "/api/insufficient"}:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            try:
                payload = self._read_payload()
                scenario_id = str(payload.get("scenario_id") or "")
                confirmation = str(payload.get("confirmation") or "")
                if path == "/api/approve":
                    session.approve_suggestion(
                        scenario_id,
                        confirmation=confirmation,
                    )
                elif path == "/api/insufficient":
                    session.record_insufficient_evidence(
                        scenario_id,
                        confirmation=confirmation,
                    )
                else:
                    categories = payload.get("categories")
                    label = payload.get("label")
                    if not isinstance(categories, list):
                        raise ValueError("categories must be a list")
                    if not isinstance(label, Mapping):
                        raise ValueError("label must be an object")
                    session.record_correction(
                        scenario_id,
                        confirmation=confirmation,
                        categories=categories,
                        label=label,
                    )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            self._send_json(
                HTTPStatus.OK,
                {"item": session.next_pending(), "progress": session.progress()},
            )

        def _read_payload(self) -> dict[str, object]:
            content_type = self.headers.get_content_type()
            if content_type != "application/json":
                raise ValueError("content type must be application/json")
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                raise ValueError("content length is required")
            try:
                length = int(raw_length)
            except ValueError as error:
                raise ValueError("content length is invalid") from error
            if length <= 0 or length > _MAX_REQUEST_BYTES:
                raise ValueError("request body size is invalid")
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError("request body must be an object")
            return value

        def _send_json(self, status: HTTPStatus, value: object) -> None:
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
                "frame-ancestors 'none'; form-action 'self'",
            )
            self.end_headers()
            self.wfile.write(body)

    return ReviewRequestHandler


def create_review_server(
    session: ReviewSession,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    static_root: str | Path = _STATIC_ROOT,
) -> ThreadingHTTPServer:
    """Create an HTTP server that can never bind to a non-loopback address."""

    if not _is_loopback(host):
        raise ValueError("review server host must be a loopback address")
    if type(port) is not int or not 0 <= port <= 65_535:
        raise ValueError("review server port is invalid")
    handler = _handler_for(session, Path(static_root))
    return ThreadingHTTPServer((host, port), handler)


__all__ = ("create_review_server",)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the loopback-only Groupmate evaluation review page."
    )
    parser.add_argument(
        "--queue",
        default="eval/private/target_review_queue.jsonl",
        help="de-identified fixed review queue JSONL",
    )
    parser.add_argument(
        "--suggestions",
        default="eval/private/target_label_suggestions.jsonl",
        help="low-confidence suggestion JSONL",
    )
    parser.add_argument(
        "--decisions",
        default="eval/private/target_review_decisions.jsonl",
        help="human decision JSONL to create or resume",
    )
    parser.add_argument("--reviewer-id", required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    session = ReviewSession(
        args.queue,
        args.suggestions,
        decisions_path=args.decisions,
        reviewer_id=args.reviewer_id,
    )
    server = create_review_server(session, host="127.0.0.1", port=args.port)
    print(
        f"Groupmate review page: http://127.0.0.1:{server.server_port}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

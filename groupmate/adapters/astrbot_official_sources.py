"""SHADOW-only official-source probing through an injected AstrBot capability.

The adapter deliberately has no HTTP client.  A host deployment may expose an
async ``context.fetch_official_source(url=..., timeout_seconds=...)`` capability;
without it the probe fails closed as unavailable.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Mapping

from ..social_runtime.knowledge.contracts import SourceClass, SourceEvidence
from ..social_runtime.knowledge.sources import (
    OfficialProbeRequest,
    OfficialProbeResult,
    SafeSourceUrlPolicy,
)


_MAX_PAGE_CHARS = 1_000_000
_METADATA_KEYS = frozenset(
    {
        "publisher",
        "og:site_name",
        "application-name",
        "og:title",
        "twitter:title",
        "article:published_time",
        "published_time",
        "date",
    }
)


class _MetadataParser(HTMLParser):
    """Read only deterministic document metadata, never page body text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: dict[str, str] = {}
        self._in_title = False
        self._title: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.casefold()
        if name == "title":
            self._in_title = True
            return
        if name != "meta":
            return
        attributes = {
            str(key).casefold(): str(value or "")
            for key, value in attrs
        }
        key = str(attributes.get("property") or attributes.get("name") or "").casefold()
        if key in _METADATA_KEYS and key not in self.values:
            self.values[key] = attributes.get("content", "")

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title.append(data)

    def title(self) -> str:
        return _normalized(
            self.values.get("og:title")
            or self.values.get("twitter:title")
            or "".join(self._title)
        )

    def publisher(self) -> str:
        return _normalized(
            self.values.get("publisher")
            or self.values.get("og:site_name")
            or self.values.get("application-name")
        )

    def published_at(self) -> int | None:
        value = _normalized(
            self.values.get("article:published_time")
            or self.values.get("published_time")
            or self.values.get("date")
        )
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        timestamp = int(parsed.astimezone(timezone.utc).timestamp())
        return timestamp if timestamp >= 0 else None


@dataclass(frozen=True)
class _FetchedMetadata:
    title: str
    publisher: str
    published_at: int | None
    content_hash: str


def _normalized(value: object) -> str:
    return " ".join(str(value or "").split())


def _value(response: object, name: str) -> object:
    if isinstance(response, Mapping):
        return response.get(name)
    return getattr(response, name, None)


class AstrBotOfficialSourceProbe:
    """Probe registered URLs with an optional host-provided fetch capability."""

    def __init__(
        self,
        context: object,
        url_policy: SafeSourceUrlPolicy,
        timeout_seconds: float,
    ) -> None:
        timeout = float(timeout_seconds)
        if timeout <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.context = context
        self.url_policy = url_policy
        self.timeout_seconds = timeout

    async def probe(self, request: OfficialProbeRequest) -> OfficialProbeResult:
        fetch = getattr(self.context, "fetch_official_source", None)
        if not callable(fetch):
            return self._result(request, "unavailable", (), (), "official_probe_unavailable")

        fetched: dict[str, tuple[_FetchedMetadata | None, str]] = {}
        for source in request.sources:
            if source.canonical_url not in fetched:
                fetched[source.canonical_url] = await self._fetch_metadata(
                    fetch, source.canonical_url
                )

        evidence: list[SourceEvidence] = []
        covered: list[str] = []
        failures: list[str] = []
        for source in request.sources:
            metadata, failure = fetched[source.canonical_url]
            if metadata is None or metadata.publisher != source.publisher:
                failures.append(failure or "failed")
                continue
            try:
                evidence.append(
                    SourceEvidence.create(
                        evidence_id=(
                            "official:{}:{}".format(
                                source.source_id,
                                metadata.content_hash[:16],
                            )
                        ),
                        source_id=source.source_id,
                        canonical_url=source.canonical_url,
                        domain=source.domain,
                        publisher=source.publisher,
                        source_class=SourceClass.OFFICIAL,
                        title=metadata.title,
                        published_at=metadata.published_at,
                        fetched_at=request.requested_at,
                        evidence_excerpt=metadata.title,
                        content_hash=metadata.content_hash,
                    )
                )
            except ValueError:
                failures.append("failed")
                continue
            covered.append(source.source_id)

        if len(covered) == len(request.sources):
            return self._result(request, "complete", evidence, covered, None)
        if evidence:
            return self._result(
                request,
                "partial",
                evidence,
                covered,
                "official_probe_partial",
            )
        if "timed_out" in failures:
            code = "official_probe_timed_out"
            return self._result(request, "timed_out", (), (), code)
        if "unavailable" in failures:
            code = "official_probe_unavailable"
            return self._result(request, "unavailable", (), (), code)
        return self._result(
            request, "failed", (), (), "official_probe_failed"
        )

    async def _fetch_metadata(
        self, fetch: object, canonical_url: str
    ) -> tuple[_FetchedMetadata | None, str]:
        async def call() -> object:
            response = fetch(url=canonical_url, timeout_seconds=self.timeout_seconds)
            return await response if inspect.isawaitable(response) else response

        try:
            response = await asyncio.wait_for(call(), timeout=self.timeout_seconds)
        except (TimeoutError, asyncio.TimeoutError):
            return None, "timed_out"
        except Exception:
            return None, "failed"

        final_url = _value(response, "final_url") or canonical_url
        try:
            if self.url_policy.canonicalize(final_url) != canonical_url:
                return None, "failed"
        except ValueError:
            return None, "failed"
        status_code = _value(response, "status_code")
        if status_code is not None and status_code != 200:
            return None, "failed"
        body = _value(response, "body")
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")
        if not isinstance(body, str) or not body or len(body) > _MAX_PAGE_CHARS:
            return None, "failed"
        parser = _MetadataParser()
        try:
            parser.feed(body)
            parser.close()
        except Exception:
            return None, "failed"
        title = parser.title()
        publisher = parser.publisher()
        if not title or not publisher:
            return None, "failed"
        return (
            _FetchedMetadata(
                title=title,
                publisher=publisher,
                published_at=parser.published_at(),
                content_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            ),
            "",
        )

    @staticmethod
    def _result(
        request: OfficialProbeRequest,
        status: str,
        evidence: tuple[SourceEvidence, ...] | list[SourceEvidence],
        covered_source_ids: tuple[str, ...] | list[str],
        diagnostic_code: str | None,
    ) -> OfficialProbeResult:
        return OfficialProbeResult.create(
            request=request,
            status=status,
            evidence=tuple(evidence),
            covered_source_ids=tuple(covered_source_ids),
            diagnostic_code=diagnostic_code,
        )


__all__ = ("AstrBotOfficialSourceProbe",)

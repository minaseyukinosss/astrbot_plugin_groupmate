from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import pytest


def _module():
    from groupmate.social_runtime.knowledge import sources

    return sources


def _contracts():
    from groupmate.social_runtime.knowledge import contracts

    return contracts


@pytest.mark.parametrize(
    "url",
    (
        "ftp://example.com/news",
        "https://user:secret@example.com/news",
        "https://example.com/news#fragment",
        "http://127.0.0.1/news",
        "http://10.0.0.1/news",
        "http://172.16.0.1/news",
        "http://192.168.1.1/news",
        "http://169.254.169.254/latest/meta-data",
        "http://2130706433/news",
        "http://0x7f000001/news",
        "http://127.1/news",
        "http://127。0。0。1/news",
        "http://１２７.０.０.１/news",
        "http://metadata.google.internal/computeMetadata/v1/",
        "https://[::1]/news",
    ),
)
def test_url_policy_rejects_unsafe_source_urls(url):
    with pytest.raises(ValueError, match="safe public HTTP"):
        _module().SafeSourceUrlPolicy().canonicalize(url)


def test_url_policy_strips_tracking_and_bounds_url_length():
    policy = _module().SafeSourceUrlPolicy()

    assert policy.canonicalize(
        "HTTPS://Example.COM:443/news?id=7&utm_source=chat&lang=zh-cn"
    ) == "https://example.com/news?id=7&lang=zh-cn"
    with pytest.raises(ValueError, match="URL"):
        policy.canonicalize("https://example.com/" + "a" * 2050)


@pytest.mark.parametrize(
    "url",
    (
        "http://2130706433/private?utm_source=leak",
        "http://127。0。0。1/private",
        "http://１２７.０.０.１/private",
    ),
)
def test_source_evidence_uses_the_same_canonical_url_policy(url):
    with pytest.raises(ValueError, match="safe public HTTP"):
        _evidence(
            canonical_url=url,
            domain=urlsplit(url).hostname,
        )

    evidence = _evidence(
        canonical_url=(
            "https://ys.mihoyo.com/main/news/detail/1"
            "?id=7&utm_source=tracking"
        )
    )
    assert evidence.canonical_url.endswith("?id=7")


def _definition(*, required=True):
    return _module().OfficialSourceDefinition.create(
        source_id="source:genshin:official-news",
        game_entity_id="game:genshin-impact",
        publisher="原神官方",
        canonical_url="https://ys.mihoyo.com/main/news",
        required=required,
    )


def _request(*, sources=None):
    return _module().OfficialProbeRequest.create(
        request_id="probe:1",
        game_entity_id="game:genshin-impact",
        query_intent="next_version_official",
        sources=(_definition(),) if sources is None else sources,
        region="cn",
        platform="all",
        requested_at=100,
    )


def _evidence(**overrides):
    values = {
        "evidence_id": "evidence:1",
        "source_id": "source:genshin:official-news",
        "canonical_url": "https://ys.mihoyo.com/main/news/detail/1",
        "domain": "ys.mihoyo.com",
        "publisher": "原神官方",
        "source_class": "official",
        "title": "版本公告",
        "published_at": 90,
        "fetched_at": 100,
        "evidence_excerpt": "官方公开了版本前瞻信息",
        "content_hash": "b" * 64,
    }
    values.update(overrides)
    return _contracts().SourceEvidence.create(**values)


def test_official_probe_rejects_publisher_or_domain_registry_mismatch():
    request = _request()
    with pytest.raises(ValueError, match="registry"):
        _module().OfficialProbeResult.create(
            request=request,
            status="complete",
            evidence=(_evidence(publisher="仿冒发布者"),),
            covered_source_ids=("source:genshin:official-news",),
        )
    with pytest.raises(ValueError, match="registry"):
        _module().OfficialProbeResult.create(
            request=request,
            status="complete",
            evidence=(
                _evidence(
                    canonical_url="https://evil.example/news/1",
                    domain="evil.example",
                ),
            ),
            covered_source_ids=("source:genshin:official-news",),
        )


def test_complete_probe_requires_every_required_source_to_be_covered():
    required = _definition()
    optional = _module().OfficialSourceDefinition.create(
        source_id="source:genshin:optional",
        game_entity_id="game:genshin-impact",
        publisher="原神官方",
        canonical_url="https://ys.mihoyo.com/main/optional",
        required=False,
    )
    request = _request(sources=(required, optional))
    with pytest.raises(ValueError, match="required source"):
        _module().OfficialProbeResult.create(
            request=request,
            status="complete",
            evidence=(),
            covered_source_ids=(),
        )

    result = _module().OfficialProbeResult.create(
        request=request,
        status="complete",
        evidence=(),
        covered_source_ids=(required.source_id,),
    )
    assert result.status == "complete"
    assert result.evidence == ()


@pytest.mark.parametrize(
    "status", ("complete", "partial", "timed_out", "unavailable", "failed")
)
def test_probe_result_supports_only_bounded_status_semantics(status):
    request = _request()
    values = {
        "request": request,
        "status": status,
        "evidence": (),
        "covered_source_ids": (
            ("source:genshin:official-news",) if status == "complete" else ()
        ),
        "diagnostic_code": (
            None if status == "complete" else "official_probe_incomplete"
        ),
    }
    assert _module().OfficialProbeResult.create(**values).status == status
    with pytest.raises(ValueError, match="status"):
        _module().OfficialProbeResult.create(**{**values, "status": "success"})


def test_probe_port_is_async_and_has_no_side_effectful_default():
    module = _module()

    class Probe(module.OfficialSourceProbePort):
        async def probe(self, request):
            return module.OfficialProbeResult.create(
                request=request,
                status="complete",
                evidence=(),
                covered_source_ids=("source:genshin:official-news",),
            )

    result = asyncio.run(Probe().probe(_request()))
    assert result.status == "complete"

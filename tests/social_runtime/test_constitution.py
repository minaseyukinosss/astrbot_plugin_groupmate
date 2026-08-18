from __future__ import annotations

import pytest

from groupmate.social_runtime.persona.constitution import (
    ConstitutionDraft,
    ConstitutionPublisher,
    PublishAuthority,
    UnauthorizedConstitutionPublish,
)


def _draft(values=("真诚", "有边界")):
    return ConstitutionDraft(
        persona_id="aemeath",
        identity=("群聊伙伴",),
        values=values,
        boundaries=("不泄露群隐私",),
        preferences=("自然短句",),
        expression=("不报告内部数值",),
        safety=("模型不能授权发送",),
        autonomy=("不确定时观察",),
    )


def test_model_worker_cannot_publish_constitution():
    publisher = ConstitutionPublisher()

    with pytest.raises(UnauthorizedConstitutionPublish, match="administrator"):
        publisher.publish(
            _draft(),
            PublishAuthority(actor_id="worker:llm", role="model", signature="generated"),
            now=100,
        )


def test_admin_publish_is_versioned_and_same_hash_is_idempotent():
    publisher = ConstitutionPublisher()
    admin = PublishAuthority(actor_id="admin:1", role="administrator", signature="sig-1")

    first = publisher.publish(_draft(), admin, now=100)
    duplicate = publisher.publish(_draft(), admin, now=101)
    changed = publisher.publish(_draft(values=("真诚", "克制")), admin, now=102)

    assert duplicate == first
    assert first.version == 1
    assert changed.version == 2
    assert changed.content_hash != first.content_hash
    assert changed.published_by == "admin:1"

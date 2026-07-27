"""隐私分类与自动接受门禁。"""

from __future__ import annotations

from groupmate.memory.privacy import PrivacyClassifier, allows_auto_accept
from groupmate.models import Sensitivity


def test_sensitive_texts_are_classified_and_blocked():
    classifier = PrivacyClassifier()
    cases = (
        ("我的密码是 abc123", Sensitivity.CREDENTIAL),
        ("身份证 110101199001011234", Sensitivity.PII),
        ("我确诊抑郁症了", Sensitivity.MEDICAL),
        ("听说他其实喜欢男人", Sensitivity.THIRD_PARTY),
        ("开玩笑的别当真，我明天考试", Sensitivity.JOKE),
        ("这是未成年相关敏感", Sensitivity.MINOR),
    )
    for text, expected in cases:
        sensitivity = classifier.classify(text)
        assert sensitivity is expected
        assert allows_auto_accept(sensitivity) is False


def test_non_sensitive_preference_is_allowed():
    classifier = PrivacyClassifier()
    sensitivity, ok = classifier.gate("我喜欢吃草莓蛋糕")
    assert sensitivity is Sensitivity.NONE
    assert ok is True

"""敏感分类与自动接受门禁。"""

from __future__ import annotations

import hashlib
import re
from typing import Tuple

from ..models import Sensitivity


def normalize_claim(claim: str) -> str:
    text = re.sub(r"\s+", " ", (claim or "").strip().lower())
    return text


def claim_hash(claim: str) -> str:
    return hashlib.sha256(normalize_claim(claim).encode("utf-8")).hexdigest()


def allows_auto_accept(sensitivity: Sensitivity) -> bool:
    return sensitivity is Sensitivity.NONE


class PrivacyClassifier:
    """规则化敏感分类：凡非 none 一律禁止自动接受。"""

    _PATTERNS = (
        (
            Sensitivity.CREDENTIAL,
            (
                r"密码",
                r"口令",
                r"验证码",
                r"token",
                r"cookie",
                r"api[_\s-]?key",
                r"access[_\s-]?key",
                r"secret",
                r"登录链接",
                r"绑定码",
            ),
        ),
        (
            Sensitivity.PII,
            (
                r"身份证",
                r"手机号",
                r"电话",
                r"住址",
                r"家庭地址",
                r"银行卡",
                r"信用卡",
                r"护照号",
                r"\d{11}",
                r"\d{17}[\dXx]",
            ),
        ),
        (
            Sensitivity.MEDICAL,
            (
                r"抑郁症",
                r"精神分裂",
                r"癌症",
                r"艾滋",
                r"HIV",
                r"怀孕",
                r"流产",
                r"确诊",
                r"病历",
            ),
        ),
        (
            Sensitivity.POLITICAL,
            (
                r"党员",
                r"入党",
                r"选举",
                r"政见",
                r"宗教",
                r"信仰",
            ),
        ),
        (
            Sensitivity.SEXUAL,
            (
                r"性取向",
                r"同性恋",
                r"出柜",
                r"性生活",
            ),
        ),
        (
            Sensitivity.MINOR,
            (
                r"未成年",
                r"小学生",
                r"初中生",
                r"儿童色情",
            ),
        ),
        (
            Sensitivity.THIRD_PARTY,
            (
                r"听说他",
                r"听说她",
                r"据说他",
                r"据说她",
                r"别人说",
                r"有人说他",
                r"有人说她",
            ),
        ),
        (
            Sensitivity.JOKE,
            (
                r"开玩笑",
                r"骗你的",
                r"反讽",
                r"讽刺一下",
                r"假的啦",
                r"别当真",
            ),
        ),
    )

    def classify(self, text: str) -> Sensitivity:
        lowered = (text or "").strip().lower()
        if not lowered:
            return Sensitivity.NONE
        for sensitivity, patterns in self._PATTERNS:
            for pattern in patterns:
                if re.search(pattern, lowered, flags=re.IGNORECASE):
                    return sensitivity
        return Sensitivity.NONE

    def gate(self, text: str) -> Tuple[Sensitivity, bool]:
        sensitivity = self.classify(text)
        return sensitivity, allows_auto_accept(sensitivity)

"""ReplyMode（回复模式）选择与爱弥斯输出防火墙约束。"""

from groupmate.core.intent import constraints_for, select_reply_mode
from groupmate.models import ReplyMode
from groupmate.persona.aemeath.output_firewall import AemeathOutputFirewall


def test_select_modes():
    assert select_reply_mode("这个怎么弄") is ReplyMode.HELP_DETAIL
    assert select_reply_mode("你给我滚") is ReplyMode.BOUNDARY
    assert select_reply_mode("哈哈") is ReplyMode.SHORT_SOCIAL


def test_firewall_allows_longer_help_mode():
    firewall = AemeathOutputFirewall()
    longish = "先打开面板再点养成然后强化，确认材料够了就点一次。" * 4
    assert len(longish) > 60
    short_mode = firewall.validate(
        longish,
        (),
        reply_mode=ReplyMode.SHORT_SOCIAL,
    )
    help_mode = firewall.validate(
        longish[:160],
        (),
        reply_mode=ReplyMode.HELP_DETAIL,
    )
    assert "too_long" in short_mode.codes
    assert constraints_for(ReplyMode.HELP_DETAIL).max_chars == 180
    assert help_mode.accepted or "too_many_sentences" in help_mode.codes

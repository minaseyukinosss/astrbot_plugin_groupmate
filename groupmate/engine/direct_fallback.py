"""Deterministic minimum replies for required direct participation."""

from __future__ import annotations

from typing import Sequence

from ..core.response_act import ResponseAct
from ..social.affinity import ResponsePosture


class DirectFallbackComposer:
    """DirectFallbackComposer（直接回应降级组装器）。"""

    # 按爱弥斯会说出口的短句写：校园闲聊、先点亮、会打闹；不写状态汇报/客服腔
    _BY_ACT = {
        ResponseAct.ACKNOWLEDGE: {
            ResponsePosture.FIRM: "在",
            ResponsePosture.RESERVED: "在",
            ResponsePosture.POLITE: "在呢",
            ResponsePosture.WARM: "我在呀",
            ResponsePosture.CLOSE: "在啦",
        },
        ResponseAct.ANSWER: {
            ResponsePosture.FIRM: "这会儿答不上",
            ResponsePosture.RESERVED: "这会儿想不起来",
            ResponsePosture.POLITE: "这会儿想不起来呀",
            ResponsePosture.WARM: "脑子卡住了呀",
            ResponsePosture.CLOSE: "等我缓缓再答呀",
        },
        ResponseAct.CLARIFY: {
            ResponsePosture.FIRM: "再说清楚点",
            ResponsePosture.RESERVED: "具体点呢",
            ResponsePosture.POLITE: "再说清楚点呀",
            ResponsePosture.WARM: "再跟我说清楚点呀",
            ResponsePosture.CLOSE: "说具体点嘛",
        },
        ResponseAct.RECIPROCATE: {
            ResponsePosture.FIRM: "收到了",
            ResponsePosture.RESERVED: "收到了",
            ResponsePosture.POLITE: "谢谢你呀",
            ResponsePosture.WARM: "谢谢你啦",
            ResponsePosture.CLOSE: "我收下啦",
        },
        ResponseAct.PLAYFUL_REPLY: {
            ResponsePosture.FIRM: "说吧",
            ResponsePosture.RESERVED: "叫我干嘛",
            ResponsePosture.POLITE: "找我呀",
            ResponsePosture.WARM: "好啦一直叫我干嘛呀",
            ResponsePosture.CLOSE: "故意叫我玩呢",
        },
        ResponseAct.BOUNDARY: {
            ResponsePosture.FIRM: "别一直叫",
            ResponsePosture.RESERVED: "别空叫了",
            ResponsePosture.POLITE: "别光叫名字呀",
            ResponsePosture.WARM: "好啦有话说嘛",
            ResponsePosture.CLOSE: "好啦别光逗我",
        },
        ResponseAct.TASK_HANDOFF: {
            ResponsePosture.FIRM: "还在弄",
            ResponsePosture.RESERVED: "还在弄",
            ResponsePosture.POLITE: "还在弄呢",
            ResponsePosture.WARM: "还在弄啦 稍等",
            ResponsePosture.CLOSE: "还在弄着呢 稍等呀",
        },
        ResponseAct.TASK_UNSUPPORTED: {
            ResponsePosture.FIRM: "这个我搞不定",
            ResponsePosture.RESERVED: "这个我搞不定",
            ResponsePosture.POLITE: "这个我搞不定呀",
            ResponsePosture.WARM: "这个我真搞不定呀",
            ResponsePosture.CLOSE: "这个我真帮不上呀",
        },
        ResponseAct.VISUAL_REACTION: {
            ResponsePosture.FIRM: "看到了",
            ResponsePosture.RESERVED: "看到了",
            ResponsePosture.POLITE: "我看到了呀",
            ResponsePosture.WARM: "看到啦",
            ResponsePosture.CLOSE: "收到图啦",
        },
    }

    # 被戳：同学打闹嫌弃带笑，亲近会回手
    _POKE_DIRECT_BY_ACT = {
        ResponseAct.PLAYFUL_REPLY: {
            ResponsePosture.FIRM: "别戳",
            ResponsePosture.RESERVED: "干嘛戳我",
            ResponsePosture.POLITE: "别戳我呀",
            ResponsePosture.WARM: "好啦别戳啦",
            ResponsePosture.CLOSE: "再戳我可回手啦",
        },
        ResponseAct.BOUNDARY: {
            ResponsePosture.FIRM: "别一直戳",
            ResponsePosture.RESERVED: "够啦",
            ResponsePosture.POLITE: "别戳啦",
            ResponsePosture.WARM: "真别一直戳呀",
            ResponsePosture.CLOSE: "好啦真别戳了",
        },
    }
    # 跟风：旁观凑热闹，像一起起哄
    _POKE_BYSTANDER_BY_ACT = {
        ResponseAct.PLAYFUL_REPLY: {
            ResponsePosture.FIRM: "倒是闲",
            ResponsePosture.RESERVED: "倒是闲",
            ResponsePosture.POLITE: "这也太闲了吧",
            ResponsePosture.WARM: "热闹呀",
            ResponsePosture.CLOSE: "那我戳一下",
        },
        ResponseAct.BOUNDARY: {
            ResponsePosture.FIRM: "别闹了",
            ResponsePosture.RESERVED: "差不多了",
            ResponsePosture.POLITE: "差不多了吧",
            ResponsePosture.WARM: "行了别闹啦",
            ResponsePosture.CLOSE: "行了歇歇吧",
        },
    }

    def compose(
        self,
        act: ResponseAct,
        posture: ResponsePosture,
        reason_codes: Sequence[str] = (),
    ) -> str:
        """compose（组装降级回应）：按行为与好感姿态返回固定短句。"""

        resolved_act = act if isinstance(act, ResponseAct) else ResponseAct(str(act))
        resolved_posture = (
            posture
            if isinstance(posture, ResponsePosture)
            else ResponsePosture(str(posture))
        )
        reasons = {str(item) for item in reason_codes or ()}
        if "poke_bystander" in reasons:
            table = self._POKE_BYSTANDER_BY_ACT.get(resolved_act)
            if table is not None:
                return table[resolved_posture]
        if reasons & {"poke_spam", "poke_direct"}:
            table = self._POKE_DIRECT_BY_ACT.get(resolved_act)
            if table is not None:
                return table[resolved_posture]
        return self._BY_ACT[resolved_act][resolved_posture]

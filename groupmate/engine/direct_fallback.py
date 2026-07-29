"""Deterministic minimum replies for required direct participation."""

from __future__ import annotations

from ..core.response_act import ResponseAct
from ..social.affinity import ResponsePosture


class DirectFallbackComposer:
    """DirectFallbackComposer（直接回应降级组装器）。"""

    _BY_ACT = {
        ResponseAct.ACKNOWLEDGE: {
            ResponsePosture.FIRM: "在。",
            ResponsePosture.RESERVED: "在。",
            ResponsePosture.POLITE: "在呢。",
            ResponsePosture.WARM: "我在呀。",
            ResponsePosture.CLOSE: "在啦。",
        },
        ResponseAct.ANSWER: {
            ResponsePosture.FIRM: "看到了，但我这会儿答不上来。",
            ResponsePosture.RESERVED: "看到了，但我这会儿答不上来。",
            ResponsePosture.POLITE: "看到了，不过我这会儿答不上来。",
            ResponsePosture.WARM: "看到了，但这会儿没答好呀。",
            ResponsePosture.CLOSE: "看到了，先让我缓一下呀。",
        },
        ResponseAct.CLARIFY: {
            ResponsePosture.FIRM: "信息不够，补全再说。",
            ResponsePosture.RESERVED: "还缺关键信息，补全再说。",
            ResponsePosture.POLITE: "还缺关键信息，补全再说呀。",
            ResponsePosture.WARM: "还差一点关键信息，补给我呀。",
            ResponsePosture.CLOSE: "还差一点信息，补给我再继续呀。",
        },
        ResponseAct.RECIPROCATE: {
            ResponsePosture.FIRM: "嗯，知道了。",
            ResponsePosture.RESERVED: "嗯，收到了。",
            ResponsePosture.POLITE: "嗯，心意收到了。",
            ResponsePosture.WARM: "嘿嘿，心意收到了呀。",
            ResponsePosture.CLOSE: "好啦，你的心意我收到了。",
        },
        ResponseAct.PLAYFUL_REPLY: {
            ResponsePosture.FIRM: "有事直说。",
            ResponsePosture.RESERVED: "叫到了，有事直说。",
            ResponsePosture.POLITE: "叫到了，说正事吧。",
            ResponsePosture.WARM: "好啦，叫这么多次做什么呀。",
            ResponsePosture.CLOSE: "一直叫我，故意逗我呢。",
        },
        ResponseAct.BOUNDARY: {
            ResponsePosture.FIRM: "别一直空叫我。",
            ResponsePosture.RESERVED: "有事直说，别一直叫。",
            ResponsePosture.POLITE: "有事直接说吧，别只叫名字。",
            ResponsePosture.WARM: "好啦，有事就直说呀。",
            ResponsePosture.CLOSE: "好啦，别光顾着逗我，说正事。",
        },
        ResponseAct.TASK_HANDOFF: {
            posture: "看到了，这件事还在处理中。"
            for posture in ResponsePosture
        },
        ResponseAct.TASK_UNSUPPORTED: {
            posture: "这个我现在做不了。"
            for posture in ResponsePosture
        },
        ResponseAct.VISUAL_REACTION: {
            posture: "图我看到了。"
            for posture in ResponsePosture
        },
    }

    def compose(self, act: ResponseAct, posture: ResponsePosture) -> str:
        """compose（组装降级回应）：按行为与好感姿态返回固定短句。"""

        resolved_act = act if isinstance(act, ResponseAct) else ResponseAct(str(act))
        resolved_posture = (
            posture
            if isinstance(posture, ResponsePosture)
            else ResponsePosture(str(posture))
        )
        return self._BY_ACT[resolved_act][resolved_posture]

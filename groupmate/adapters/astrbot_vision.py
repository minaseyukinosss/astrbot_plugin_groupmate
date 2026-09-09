"""AstrBot-backed vision client for sticker judgment and captioning."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping

from ..social_runtime.stickers.vision import parse_json_object

_PERSONA_BRIEF_LIMIT = 360
_JUDGE_SYSTEM = (
    "你在给群聊表情图鉴做入站判定。只看图，判断它是不是拿来回消息的表情包。"
    "算：夸张表情、对话气泡、白底或透明底反应图、角色或动物摆姿势、软萌卖萌、"
    "配短句大字、熊猫头、抽象画风、常见网络梗模板。可爱类型同样算，不要只认梗图。"
    "如果图是从左到右排列的多格，那是同一张动图的连续帧，不要当成拼图截图或多张无关图。"
    "拿不准但看起来就是群里拿来回一句的，判true。"
    "不算：真人照片、风景、食物实拍、证件、聊天长截图、文档、二维码。"
    "禁止外貌、年龄、真实身份、OCR长文、文件名猜测。"
    "只输出JSON对象，不要Markdown。"
    '{"is_sticker":true,"reason":"软萌动物歪头卖萌"}'
    "is_sticker必须是JSON布尔；reason不超过40字，写判定依据，不要描写人物。"
)
_JUDGE_PROMPT = "判定这张图是不是表情包。结合画面，可爱反应和网络梗都算。"
_CAPTION_SYSTEM = (
    "你在给群聊表情图鉴写卡片。闲聊会用含义和适用词去匹配这名人格刚写出的回复，"
    "词面越近越容易配上。请一次写对。"
    "读图顺序：先看画面动作；再用当前人格口吻写成她自己会发出去的那句话；"
    "能结合网络梗就结合，认不出或这是软萌卖萌就写具体动作，不要硬套梗，也不要只写「这是一个梗」。"
    "如果图是从左到右排列的多格，那是同一张动图从早到晚的帧，按动作变化写一条连贯含义，"
    "用最后一格定口气，不要当成多张无关图。"
    "禁止外貌、年龄、真实身份、URL、长数字、路径。"
    "禁止整句套话：有趣、表情包、梗图、搞笑、反应、一张图。整句只写「可爱」也不行。"
    "只输出JSON对象，不要Markdown。"
    "meaning：8到48字。看得见的动作或台词，加上这名人格嘴里会带出的那句话。"
    "换成另一张图应不成立。像回消息，不像分类。"
    "好：摊手无奈我也没办法；猫猫歪头蹭蹭求抱抱；熊猫头无语我真服了。"
    "坏：一张搞笑表情包；可爱；无奈；这是一个网络梗。"
    "use_when最多3条，写成她回复里可能出现的短词。软萌可用卖萌、抱抱、喜欢；有梗再用梗词。"
    "do_not_use最多3条，回复里出现这些词就不该配。"
    "attitudes取0到3个，看不准就空数组："
    "amused好笑,tease调侃,proud得意,helpless无奈,confused懵,startle惊讶,"
    "wronged委屈,refuse拒绝,agree同意,acknowledge附和,warm温暖,close收束。"
    "软萌优先warm、amused；吐槽再用tease、helpless。"
    "affection_floor是发给谁才合适，和attitudes无关，不要因为好笑或调侃就抬高。"
    "只能是0谁都行、10至少认识、30至少熟悉、55至少亲近、80仅默契。默认0。"
    "只有这张图发给陌生人会显得太亲昵、太私密或过界才抬高；普通反应保持0。"
    "intensity：0到100整数，普通约40，越夸张越大。"
    "无奈例："
    '{"meaning":"摊手无奈我也没办法","use_when":["我也没办法","做不到","算了"],'
    '"do_not_use":["认真技术帮助","道歉"],"attitudes":["helpless"],'
    '"affection_floor":0,"intensity":30}'
    "软萌例："
    '{"meaning":"猫猫歪头蹭蹭求抱抱","use_when":["抱抱","喜欢","想你"],'
    '"do_not_use":["认真技术帮助","拒绝"],"attitudes":["warm"],'
    '"affection_floor":0,"intensity":40}'
)
_CAPTION_PROMPT = (
    "按系统说明为这张表情写卡片。先看画面，能结合网络梗就结合，软萌按动作写，"
    "再用当前人格会怎么说写成含义。"
)


def persona_brief_for_stickers(
    profile: Mapping[str, object], *, name: str
) -> str:
    identity = profile.get("identity")
    expression = profile.get("expression")
    social = profile.get("social")
    identity = identity if isinstance(identity, Mapping) else {}
    expression = expression if isinstance(expression, Mapping) else {}
    social = social if isinstance(social, Mapping) else {}
    display = str(name or identity.get("name") or "Groupmate").strip()[:24]
    pieces = [
        f"你是{display}，群聊里的一员，这张图是你自己会发出去的",
        identity.get("role"),
        expression.get("tone"),
        expression.get("language_habits"),
        social.get("stance"),
        social.get("culture_adaptation"),
    ]
    normalized: list[str] = []
    for piece in pieces:
        text = str(piece or "").strip()
        if not text:
            continue
        if text[-1] not in "。！？；":
            text += "。"
        normalized.append(text)
    return "".join(normalized)[:_PERSONA_BRIEF_LIMIT]


class AstrBotVisionClient:
    def __init__(
        self,
        context: object,
        provider_id: str,
        *,
        timeout_seconds: float = 25,
        persona_brief_loader: Callable[[], str] | None = None,
    ) -> None:
        provider = str(provider_id or "").strip()
        if not provider:
            raise ValueError("vision provider must not be empty")
        timeout = float(timeout_seconds)
        if timeout < 5 or timeout > 60:
            raise ValueError("vision timeout_seconds must be between 5 and 60")
        if not callable(getattr(context, "llm_generate", None)):
            raise ValueError("AstrBot context cannot generate vision completions")
        self.context = context
        self.provider_id = provider
        self.timeout_seconds = timeout
        self._persona_brief_loader = persona_brief_loader

    async def judge(self, *, image_data_uri: str) -> Mapping[str, object]:
        return await self._complete(_JUDGE_SYSTEM, _JUDGE_PROMPT, image_data_uri)

    async def caption(self, *, image_data_uri: str) -> Mapping[str, object]:
        prompt = _CAPTION_PROMPT
        brief = ""
        if self._persona_brief_loader is not None:
            brief = str(self._persona_brief_loader() or "").strip()
        if brief:
            prompt = f"当前人格：{brief}\n{prompt}"
        return await self._complete(_CAPTION_SYSTEM, prompt, image_data_uri)

    async def _complete(
        self, system_prompt: str, prompt: str, image_data_uri: str
    ) -> dict[str, object]:
        uri = str(image_data_uri or "").strip()
        if not uri.startswith("data:image/"):
            raise ValueError("vision image must be a data URI")
        response = await asyncio.wait_for(
            self.context.llm_generate(
                chat_provider_id=self.provider_id,
                system_prompt=system_prompt,
                prompt=prompt,
                image_urls=[uri],
                temperature=0.1,
            ),
            timeout=self.timeout_seconds,
        )
        return parse_json_object(_response_text(response))


def _response_text(response: object) -> str:
    if isinstance(response, str):
        return response.strip()
    text = str(getattr(response, "completion_text", "") or "").strip()
    if not text:
        raise ValueError("vision model returned empty content")
    return text


__all__ = ("AstrBotVisionClient", "persona_brief_for_stickers")

"""Persona-aware short mail body composition.

Mail copy always follows the *current* persona system prompt and display name;
no fixed character identity is assumed. Output must look like a real email body,
not a one-line group-chat bubble.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from .smtp_client import normalize_mail_body


async def compose_mail(
    *,
    context: Any,
    provider_id: str,
    persona_system: str,
    theme: str,
    subject: str,
    body: str,
    recipient_label: str = "群友",
    tease: bool = False,
    character_name: str = "",
) -> Tuple[str, str]:
    cleaned_subject = str(subject or "").strip()
    cleaned_body = str(body or "").strip()
    name = str(character_name or "").strip()
    recipient = str(recipient_label or "").strip() or "群友"

    if not provider_id or context is None:
        return _fallback(
            theme,
            cleaned_subject,
            cleaned_body,
            tease=tease,
            character_name=name,
            recipient_label=recipient,
        )

    prompt_lines = [
        "你正在以当前人格本人的身份写一封很短的 QQ 邮件（纯文本）。",
        "邮件主题和正文都必须是当前人格会说出口的话：口吻、用词、节奏、自称都要贴合人格。",
        "禁止客服腔、公文腔、助手腔；禁止「作为 AI」「根据您的请求」这类说法。",
        "不要提工具、插件、系统、模型或机器人。",
        "只输出 JSON：{\"subject\":\"...\",\"body\":\"...\"}",
        "主题不超过 40 字，且不要换行。",
        "正文必须是邮件版式，禁止整封挤成一行群聊气泡。结构：",
        "1）第一行称呼（可用收件称呼）",
        "2）空一行",
        "3）正文 1～3 段，段与段之间空一行",
        "4）空一行",
        "5）署名（当前人格自称）",
        "正文总长不超过 400 字；可用 \\n 表示换行。",
        "署名使用当前人格自称，不要写真实邮箱或系统信息。",
        "收件对象 QQ 昵称（邮件正文称呼必须用这个，不要用群名片或「你」）："
        + recipient,
    ]
    if name:
        prompt_lines.append("当前人格显示名：" + name)
    else:
        prompt_lines.append("当前人格显示名未单独给出，严格按 system 中的人格设定自称。")
    if tease:
        prompt_lines.append(
            "对方越权想给别人发邮件；用当前人格俏皮但不辱骂地在正文里调侃对方自己。"
            "可以损、可以坏心眼、可以点破他越权，但不要写成系统通知，"
            "也不要自我介绍「这是一封调侃信/惩罚信」。"
            "仍要保持上述邮件版式。"
        )
    elif theme:
        prompt_lines.append("用户想要的主题/氛围：" + str(theme)[:200])
    else:
        prompt_lines.append("用户没指定内容，用当前人格随意写一封轻松短邮件。")
    if cleaned_subject or cleaned_body:
        prompt_lines.append(
            "用户可能给了素材或意图；必须改写成当前人格口吻与邮件版式后再输出，"
            "不要原样照抄用户原文（除非已经完全像该人格在说话）。"
        )
    if cleaned_subject:
        prompt_lines.append("用户提供的主题素材：" + cleaned_subject[:80])
    if cleaned_body:
        prompt_lines.append("用户提供的正文素材：" + cleaned_body[:800])

    persona_block = str(persona_system or "").strip()
    system_prompt = "\n".join(
        part
        for part in (
            persona_block,
            (
                "额外约束：此刻任务是写邮件。"
                "最终输出的 subject 与 body 必须完全是上述人格本人的口吻，"
                "body 必须是可阅读的邮件版式（称呼/空行/正文/署名），"
                "不要写成一条群聊短句。"
                "若人格切换，以本轮 system 中的人格为准，不要沿用其他角色。"
            ),
        )
        if part
    ) or (
        "你是当前已启用的人格。用自己的口吻写短邮件，不要客服腔，"
        "不要擅自换成别的固定角色；正文保持邮件版式。"
    )

    try:
        response = await context.llm_generate(
            chat_provider_id=provider_id,
            prompt="\n".join(prompt_lines),
            system_prompt=system_prompt,
        )
        text = str(getattr(response, "completion_text", "") or "").strip()
        parsed = _parse_json_object(text)
        if isinstance(parsed, dict):
            out_subject = str(parsed.get("subject") or "").strip()
            out_body = _coerce_body_newlines(str(parsed.get("body") or ""))
            if out_subject and out_body:
                shaped = shape_mail_body(
                    out_body,
                    recipient_label=recipient,
                    character_name=name,
                )
                return _clip_subject(out_subject), _clip_body(shaped)
    except Exception:
        pass
    return _fallback(
        theme,
        cleaned_subject,
        cleaned_body,
        tease=tease,
        character_name=name,
        recipient_label=recipient,
    )


def shape_mail_body(
    body: str,
    *,
    recipient_label: str,
    character_name: str,
) -> str:
    """Ensure plain-text mail has greeting / body / signature layout."""

    text = _coerce_body_newlines(body)
    text = normalize_mail_body(text)
    if not text:
        return text
    recipient = str(recipient_label or "").strip() or "你"
    signer = str(character_name or "").strip() or "我"
    if "\n" not in text:
        text = "{}\n\n{}\n\n——{}".format(recipient, text, signer)
        return normalize_mail_body(text)

    lines = text.split("\n")
    first = lines[0].strip()
    # If model omitted greeting, prepend recipient line.
    if recipient and recipient not in first and not first.endswith(("：", ":", "，", ",")):
        text = "{}\n\n{}".format(recipient, text)
    # If model omitted signature, append one.
    if signer and signer not in text.split("\n")[-1]:
        text = "{}\n\n——{}".format(text.rstrip(), signer)
    return normalize_mail_body(text)


def _fallback(
    theme: str,
    subject: str,
    body: str,
    *,
    tease: bool,
    character_name: str,
    recipient_label: str,
) -> Tuple[str, str]:
    name = str(character_name or "").strip() or "我"
    recipient = str(recipient_label or "").strip() or "你"
    if body.strip():
        shaped = shape_mail_body(
            body,
            recipient_label=recipient,
            character_name=name,
        )
        if subject:
            return _clip_subject(subject), _clip_body(shaped)
    if tease:
        fallback_body = (
            "{}\n\n"
            "想给别人发邮件却轮不到你呀。\n"
            "这封先塞给你自己，拆开慢慢品咯。\n\n"
            "——{}"
        ).format(recipient, name)
        return (
            _clip_subject(subject or "欸 你猜这是啥"),
            _clip_body(normalize_mail_body(fallback_body)),
        )
    theme_text = str(theme or "").strip() or "随便聊聊"
    fallback_body = (
        "{}\n\n"
        "这是我寄给你的一封{}小邮件。\n"
        "希望你今天也顺利呀。\n\n"
        "——{}"
    ).format(recipient, theme_text, name)
    return (
        _clip_subject(subject or "{}给你的{}".format(name, theme_text)),
        _clip_body(normalize_mail_body(fallback_body)),
    )


def _coerce_body_newlines(value: str) -> str:
    text = str(value or "")
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
    return text


def _clip_subject(value: str) -> str:
    return str(value or "").replace("\n", " ").replace("\r", " ").strip()[:80]


def _clip_body(value: str) -> str:
    return normalize_mail_body(value)[:800]


def _parse_json_object(text: str) -> Optional[dict]:
    import json
    import re

    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None
    except Exception:
        pass
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None

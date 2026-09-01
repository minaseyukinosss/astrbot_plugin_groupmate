#!/usr/bin/env python3
"""Build and execute a dependency-free notebook companion for the analysis."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "deep_behavior_analysis.ipynb"


def markdown(source: str) -> dict:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": source.splitlines(keepends=True),
    }


def code(source: str, count: int, namespace: dict) -> dict:
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        exec(compile(source, f"notebook-cell-{count}", "exec"), namespace)
    output = stream.getvalue()
    return {
        "cell_type": "code",
        "execution_count": count,
        "metadata": {},
        "outputs": (
            [{"name": "stdout", "output_type": "stream", "text": output.splitlines(keepends=True)}]
            if output
            else []
        ),
        "source": source.splitlines(keepends=True),
    }


def main() -> None:
    namespace: dict = {}
    cells = [
        markdown(
            "## tl;dr\n\n"
            "小维的灵活性主要来自高优先级接住直接互动、短程续聊、少量定向或环境参与，"
            "而不是随机主动发言。昵称是统一入口：先判断剩余内容属于能力还是社交，再进入对应链路。\n"
        ),
        markdown(
            "## Context & Methods\n\n"
            "- 数据范围：2026-07-23 至 2026-08-24 的目标群 JSONL 导出。\n"
            "- 分析单位：去重消息、Bot 发言轮次和昵称呼唤消息。\n"
            "- `next_bot` 是时间邻近指标，不等同于因果回复；`linked_reply` 只覆盖可解析引用关系。\n\n"
            "### Key Assumptions\n\n"
            "目标 Bot 使用 UIN `323537051` 识别；昵称分析使用“小维 / 小真寻 / 小真寻备用机”。\n"
        ),
        markdown("## Data\n"),
    ]
    cells.append(
        code(
            "from pathlib import Path\n"
            "import json\n"
            "metrics_path = Path('analysis/target_bot_20260824/output/deep_behavior_metrics.json')\n"
            "metrics = json.loads(metrics_path.read_text(encoding='utf-8'))\n"
            "print(json.dumps(metrics['source'], ensure_ascii=False, indent=2))\n",
            1,
            namespace,
        )
    )
    cells.append(markdown("## Results\n"))
    cells.append(
        code(
            "print('称呼方式 | 样本 | 30秒内出现Bot发言 | 60秒内可解析引用回复 | 中位后续发言秒数')\n"
            "print('-' * 88)\n"
            "for label, row in metrics['addressing'].items():\n"
            "    print(f\"{label} | {row['messages']} | {row['next_bot_30s_rate']:.1%} | {row['linked_reply_60s_rate']:.1%} | {row['median_next_bot_seconds']}\")\n",
            2,
            namespace,
        )
    )
    cells.append(
        code(
            "print('进入方式 | 轮次 | 占比 | P50/P90秒 | 2分钟明确回访')\n"
            "print('-' * 76)\n"
            "for row in metrics['trigger_lanes']:\n"
            "    p50 = row['median_latency_seconds']\n"
            "    p90 = row['p90_latency_seconds']\n"
            "    print(f\"{row['lane']} | {row['turns']} | {row['share']:.1%} | {p50}/{p90} | {row['explicit_followup_2m_rate']:.1%}\")\n",
            3,
            namespace,
        )
    )
    cells.append(
        code(
            "shape = metrics['turn_shape']\n"
            "style = metrics['style']\n"
            "targets = metrics['recipient_concentration']\n"
            "print(f\"平均每轮消息数: {shape['mean_messages_per_turn']}\")\n"
            "print(f\"多消息轮次: {shape['multi_message_turn_rate']:.1%}\")\n"
            "print(f\"轮次文本长度 P50/P90: {shape['median_turn_chars']}/{shape['p90_turn_chars']:.1f} 字\")\n"
            "print(f\"多轮对话链: {shape['multi_turn_dialogue_chains']}，链长 P50/P90: {shape['median_chain_turns']}/{shape['p90_chain_turns']:.1f}\")\n"
            "print(f\"文本唯一率: {style['unique_text_ratio']:.1%}，语气词覆盖: {style['soft_particle_rate']:.1%}\")\n"
            "print(f\"可解析定向对象: {targets['unique_reply_recipients']}，Top1占比: {targets['top_1_share']:.1%}\")\n",
            4,
            namespace,
        )
    )
    cells.append(
        markdown(
            "## Takeaways\n\n"
            "1. `@`、回复和高置信昵称呼唤应走确定性的直接互动通道；模型负责理解和表达，不负责否认“对方在叫我”。\n"
            "2. 昵称识别后必须再次判断剩余文本归属：外部能力优先，普通表达进入闲聊。\n"
            "3. 直接互动应打开短期对话租约，后续 3–6 轮优先延续，而不是每条消息从零判断是否参与。\n"
            "4. 环境参与保持少量、定向、受频控；冷启动主动开场维持极低频。\n"
            "5. 输出默认 1–2 条短消息，媒体是表达能力而不是核心参与机制。\n"
        )
    )
    notebook = {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    OUTPUT.write_text(json.dumps(notebook, ensure_ascii=False, indent=1), encoding="utf-8")
    loaded = json.loads(OUTPUT.read_text(encoding="utf-8"))
    if loaded.get("nbformat") != 4 or not loaded.get("cells"):
        raise RuntimeError("generated notebook failed structural validation")
    code_cells = [cell for cell in loaded["cells"] if cell.get("cell_type") == "code"]
    if not code_cells or any(cell.get("execution_count") is None for cell in code_cells):
        raise RuntimeError("generated notebook is not fully executed")
    print(f"wrote {OUTPUT} with {len(code_cells)} executed code cells")


if __name__ == "__main__":
    main()

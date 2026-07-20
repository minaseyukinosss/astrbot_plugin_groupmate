"""Stable machine-readable and Chinese human-readable evaluation reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Sequence

from .metrics import MetricReport
from .models import PredictionRecord


METRIC_LABELS = (
    ("accuracy", "严格场景准确率"),
    ("wake_recall", "直接唤醒召回率"),
    ("native_wake_bypass_rate", "原生唤醒旁路率"),
    ("command_bypass_rate", "指令旁路率"),
    ("active_precision", "主动介入精确率"),
    ("active_recall", "主动介入召回率"),
    ("false_intervention_rate", "错误插话率"),
    ("silence_accuracy", "沉默准确率"),
    ("decision_model_call_rate", "决策模型调用率"),
    ("decision_structure_success_rate", "决策结构成功率"),
)


def prediction_dict(prediction: PredictionRecord) -> Dict[str, Any]:
    return {
        "case_id": prediction.case_id,
        "expected_label": prediction.expected_label.value,
        "trigger": prediction.trigger.value,
        "action": prediction.action,
        "confidence": prediction.confidence,
        "reason_code": prediction.reason_code,
        "target_message_id": prediction.target_message_id,
        "decision_model_called": prediction.decision_model_called,
        "latency_ms": round(prediction.latency_ms, 6),
        "error_code": prediction.error_code,
        "matched": prediction.matched,
    }


def write_run_report(
    output_dir: Path,
    dataset_hash: str,
    config: Dict[str, Any],
    predictions: Sequence[PredictionRecord],
    metrics: MetricReport,
) -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "dataset_hash": dataset_hash,
        "config": config,
        "predictions": [prediction_dict(item) for item in predictions],
        "metrics": metrics.to_dict(),
    }
    (output / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Groupmate 决策评测报告",
        "",
        "- 严格样本：{}".format(metrics.strict_sample_count),
        "- 可选样本：{}".format(metrics.optional_sample_count),
        "- 数据集状态：{}".format(
            "样本充足" if metrics.sample_sufficient else "样本不足"
        ),
        "",
        "## 指标",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
    ]
    values = metrics.to_dict()
    for key, label in METRIC_LABELS:
        value = values[key]
        lines.append("| {} | {} |".format(label, _format_metric(value)))
    lines.extend(
        [
            "| P50 决策耗时 | {} ms |".format(_format_number(metrics.p50_latency_ms)),
            "| P95 决策耗时 | {} ms |".format(_format_number(metrics.p95_latency_ms)),
            "",
        ]
    )
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")


def write_comparison(baseline: Dict[str, Any], candidate: Dict[str, Any], path: Path) -> None:
    if baseline.get("dataset_hash") != candidate.get("dataset_hash"):
        raise ValueError("数据集哈希不同，不能计算配置提升")
    before = {item["case_id"]: item for item in baseline.get("predictions", [])}
    after = {item["case_id"]: item for item in candidate.get("predictions", [])}
    changed = []
    for case_id in sorted(set(before) & set(after)):
        left, right = before[case_id], after[case_id]
        if left.get("action") != right.get("action") or left.get("matched") != right.get(
            "matched"
        ):
            changed.append((case_id, left, right))
    lines = [
        "# Groupmate 配置对比报告",
        "",
        "- 变化场景：{}".format(len(changed)),
        "",
        "| 场景 | 基线 | 候选 | 结果变化 |",
        "|---|---|---|---|",
    ]
    for case_id, left, right in changed:
        result = "改善" if not left.get("matched") and right.get("matched") else "退化"
        lines.append(
            "| {} | {} | {} | {} |".format(
                case_id, left.get("action"), right.get("action"), result
            )
        )
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _format_metric(value):
    return "无数据" if value is None else "{:.2%}".format(value)


def _format_number(value):
    return "无数据" if value is None else "{:.3f}".format(value)

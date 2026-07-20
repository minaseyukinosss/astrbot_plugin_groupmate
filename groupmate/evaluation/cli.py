"""Command-line entry point for local, reproducible decision evaluation."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from ..models import Decision, GroupPolicy
from .dataset import DatasetValidationError, load_dataset
from .evaluator import DecisionEvaluator
from .metrics import calculate_metrics
from .report import write_comparison, write_run_report
from .replay import OfflineReplayRunner
from .shadow_export import export_labeled_shadow_dataset


class SafeSilenceDecisionModel:
    async def decide(self, topic, policy, memories):
        del topic, policy, memories
        return Decision.ignore("safe_silence_baseline")


def build_parser():
    parser = argparse.ArgumentParser(description="Groupmate 决策评测工具")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="校验评测数据集")
    validate.add_argument("--dataset", required=True)
    run = commands.add_parser("run", help="运行安全沉默基线评测")
    run.add_argument("--dataset", required=True)
    run.add_argument("--config", required=True)
    run.add_argument("--output", required=True)
    compare = commands.add_parser("compare", help="对比两次评测")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--candidate", required=True)
    compare.add_argument("--output", required=True)
    export = commands.add_parser(
        "export-shadow", help="把插件自行采集并标注的记录生成评测集"
    )
    export.add_argument("--database", required=True)
    export.add_argument("--output", required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            dataset = load_dataset(Path(args.dataset))
            print("数据集有效：{} 个场景，哈希 {}".format(len(dataset.cases), dataset.content_hash))
            return 0
        if args.command == "run":
            return _run(args)
        if args.command == "export-shadow":
            return _export_shadow(args)
        return _compare(args)
    except (DatasetValidationError, OSError, ValueError, KeyError) as exc:
        print("评测失败：{}".format(exc), file=sys.stderr)
        return 2


def _run(args):
    dataset = load_dataset(Path(args.dataset))
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    policy = GroupPolicy(
        aliases=tuple(config.get("aliases", ("爱弥斯", "小爱", "飞行雪绒"))),
        decision_threshold=float(config.get("decision_threshold", 0.72)),
        history_limit=int(config.get("history_limit", 100)),
    )
    evaluator = DecisionEvaluator(SafeSilenceDecisionModel(), policy)
    predictions = asyncio.run(OfflineReplayRunner(evaluator).run(dataset.cases))
    metrics = calculate_metrics(dataset.cases, predictions)
    write_run_report(Path(args.output), dataset.content_hash, config, predictions, metrics)
    return 0


def _compare(args):
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    write_comparison(baseline, candidate, Path(args.output))
    return 0


def _export_shadow(args):
    from ..memory import SQLiteMemoryStore

    store = SQLiteMemoryStore(Path(args.database))
    try:
        count = export_labeled_shadow_dataset(store, Path(args.output))
    finally:
        store.close()
    print("已生成 {} 个本地影子评测场景".format(count))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

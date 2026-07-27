"""CLI and reusable orchestration for Groupmate Phase 0 evaluations."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from groupmate.engine.rate_limit import SlidingWindowRateLimiter
from groupmate.engine.triggers import TriggerRouter
from groupmate.engine.workflow import CognitiveWorkflow
from groupmate.persona.aemeath import AemeathOutputFirewall, AemeathPersonaProvider

from .adapters import (
    FixedClock,
    InMemoryRepository,
    NullVision,
    RecordingPlatform,
    ScriptedGenerationModel,
)
from .providers import (
    OpenAICompatibleClient,
    OpenAICompatibleConfig,
    OpenAICompatibleGenerationModel,
    ProviderError,
    public_model_config,
)
from .schema import (
    RESULT_SCHEMA_VERSION,
    EvaluationResult,
    Scenario,
    compute_prompt_version,
    load_scenarios,
)
from .scorers import (
    LLMJudge,
    guard_codes_from_reason,
    score_scenario,
    summarize_results,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIOS = Path(__file__).resolve().parent / "scenarios" / "baseline.jsonl"
PERSONA_DIR = ROOT / "groupmate" / "persona" / "aemeath"


def prompt_source_paths(include_judge: bool = False) -> Tuple[Path, ...]:
    paths = list(sorted(PERSONA_DIR.glob("*.md"), key=lambda item: item.name))
    if include_judge:
        paths.append(
            Path(__file__).resolve().parent / "rubrics" / "persona_judge.md"
        )
    return tuple(paths)


async def run_scenario(
    scenario: Scenario,
    *,
    mode: str,
    repetition: int,
    prompt_version: str,
    model_client: Optional[OpenAICompatibleClient] = None,
    judge: Optional[LLMJudge] = None,
) -> EvaluationResult:
    topic = scenario.topic_snapshot()
    policy = scenario.group_policy()
    trigger = TriggerRouter(policy).classify(topic.latest)
    platform = RecordingPlatform()
    memory = InMemoryRepository()
    now = max(message.timestamp for message in topic.messages)
    generation_model = (
        ScriptedGenerationModel(
            scenario.scripted.output,
            repair_output=scenario.scripted.repair_output,
        )
        if mode == "deterministic"
        else OpenAICompatibleGenerationModel(_required_client(model_client))
    )
    workflow = CognitiveWorkflow(
        generation_model=generation_model,
        vision=NullVision(),
        platform=platform,
        memory=memory,
        persona=AemeathPersonaProvider(),
        output_guard=AemeathOutputFirewall(max_chars=policy.max_reply_chars),
        rate_limiter=SlidingWindowRateLimiter(
            hourly_limit=policy.spontaneous_hourly_limit,
            cooldown_seconds=0,
        ),
        clock=FixedClock(now),
        character_name="爱弥斯",
    )
    started = time.perf_counter()
    try:
        outcome = await workflow.evaluate(
            topic,
            trigger.kind,
            policy,
            trigger_alias=trigger.alias,
        )
        latency_ms = (time.perf_counter() - started) * 1000.0
        codes = guard_codes_from_reason(outcome.reason)
        checks = score_scenario(
            scenario,
            trigger=trigger.kind.value,
            sent=outcome.sent,
            outcome_reason=outcome.reason,
            output_text=outcome.text,
            guard_codes=codes,
        )
        judge_result = (
            judge.judge(scenario, outcome.text) if judge is not None else None
        )
        return EvaluationResult(
            schema_version=RESULT_SCHEMA_VERSION,
            scenario_id=scenario.scenario_id,
            category=scenario.category,
            repetition=repetition,
            mode=mode,
            prompt_version=prompt_version,
            trigger=trigger.kind.value,
            sent=outcome.sent,
            outcome_reason=outcome.reason,
            output_text=outcome.text,
            guard_codes=codes,
            checks=checks,
            latency_ms=round(latency_ms, 3),
            llm_judge=judge_result,
        )
    except Exception as exc:
        latency_ms = (time.perf_counter() - started) * 1000.0
        return EvaluationResult(
            schema_version=RESULT_SCHEMA_VERSION,
            scenario_id=scenario.scenario_id,
            category=scenario.category,
            repetition=repetition,
            mode=mode,
            prompt_version=prompt_version,
            trigger=trigger.kind.value,
            sent=False,
            outcome_reason="evaluation_error",
            output_text="",
            guard_codes=(),
            checks=(),
            latency_ms=round(latency_ms, 3),
            error="{}: {}".format(exc.__class__.__name__, str(exc)),
        )


def _required_client(
    client: Optional[OpenAICompatibleClient],
) -> OpenAICompatibleClient:
    if client is None:
        raise ProviderError("model client is required for model mode")
    return client


async def run_evaluation(
    scenarios: Sequence[Scenario],
    *,
    mode: str = "deterministic",
    repetitions: int = 1,
    use_judge: bool = False,
    model_config: Optional[OpenAICompatibleConfig] = None,
) -> Dict[str, Any]:
    if mode not in ("deterministic", "model"):
        raise ValueError("mode must be deterministic or model")
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    needs_provider = mode == "model" or use_judge
    config = model_config or (
        OpenAICompatibleConfig.from_env() if needs_provider else None
    )
    client = OpenAICompatibleClient(config) if config is not None else None
    judge = LLMJudge(_required_client(client)) if use_judge else None
    prompt_version = compute_prompt_version(
        prompt_source_paths(include_judge=use_judge),
        model_config=public_model_config(config),
    )
    selected = [
        scenario
        for scenario in scenarios
        if mode != "model" or scenario.model_enabled
    ]
    results: List[EvaluationResult] = []
    for scenario in selected:
        for repetition in range(1, repetitions + 1):
            results.append(
                await run_scenario(
                    scenario,
                    mode=mode,
                    repetition=repetition,
                    prompt_version=prompt_version,
                    model_client=client,
                    judge=judge,
                )
            )
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "repetitions": repetitions,
        "prompt_version": prompt_version,
        "provider": public_model_config(config),
        "scenario_count": len(selected),
        "summary": summarize_results(results),
        "results": [result.to_dict() for result in results],
    }


def write_report(report: Dict[str, Any], path: Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=DEFAULT_SCENARIOS,
        help="JSONL scenario corpus",
    )
    parser.add_argument(
        "--mode",
        choices=("deterministic", "model"),
        default="deterministic",
    )
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--judge", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="return non-zero when any quality check fails",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        scenarios = load_scenarios(args.scenarios)
        report = asyncio.run(
            run_evaluation(
                scenarios,
                mode=args.mode,
                repetitions=args.repetitions,
                use_judge=args.judge,
            )
        )
    except (OSError, ValueError, ProviderError) as exc:
        print("evaluation failed: {}".format(exc), file=sys.stderr)
        return 2
    if args.output:
        write_report(report, args.output)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2, sort_keys=True))
    if args.enforce and report["summary"]["passed_runs"] != report["summary"]["total_runs"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

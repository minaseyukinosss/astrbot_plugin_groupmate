"""Command-line orchestration for local export shadow alignment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from eval.behavior_diff import (
    PrivacyViolation,
    assert_shareable_report,
    build_diff_report,
    write_json_report,
    write_markdown_report,
)
from eval.export_ingest import ExportValidationError, load_export
from eval.reference_labeler import (
    ReferenceLabeler,
    apply_overrides,
    collect_label_reviews,
    load_overrides,
)
from eval.shadow_extract import (
    LocalIdHasher,
    extract_behavior_examples,
    load_or_create_salt,
)
from eval.shadow_models import AssociationConfidence
from eval.shadow_projector import ShadowProjector
from groupmate.persona import default_persona_registry
from groupmate.policies import BehaviorPolicy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline export shadow alignment"
    )
    parser.add_argument("--export-dir", type=Path, required=True)
    parser.add_argument("--target-uin", required=True)
    parser.add_argument("--target-alias", required=True)
    parser.add_argument("--current-alias", required=True)
    parser.add_argument("--id-salt-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--review-output", type=Path)
    parser.add_argument("--overrides", type=Path)
    return parser


def _dedupe_reviews(reviews):
    unique = []
    seen = set()
    for item in reviews:
        key = (item.sample_id, item.reason)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return tuple(unique)


def _privacy_values(ingest, args):
    identifiers = {
        str(args.target_uin or "").strip(),
        str(args.target_alias or "").strip(),
    }
    texts = set()
    for event in ingest.events:
        identifiers.update((
            event.sender_uin,
            event.sender_key,
            event.sender_name,
        ))
        if event.text:
            texts.add(event.text)
    return (
        tuple(sorted(value for value in identifiers if value)),
        tuple(sorted(texts)),
    )


def _validate_local_paths(args) -> None:
    export_root = args.export_dir.expanduser().resolve()
    writable = [
        args.id_salt_file,
        args.output,
        args.markdown_output,
        args.review_output,
    ]
    resolved = []
    for path in writable:
        if path is None:
            continue
        target = path.expanduser().resolve()
        try:
            target.relative_to(export_root)
        except ValueError:
            pass
        else:
            raise ValueError(
                "output paths must be outside the source export directory"
            )
        resolved.append(target)
    if len(resolved) != len(set(resolved)):
        raise ValueError("output paths must be distinct")
    if args.overrides is not None:
        override = args.overrides.expanduser().resolve()
        if override in resolved:
            raise ValueError("output paths must not overwrite overrides")


def write_review_queue(reviews, path: Path) -> None:
    rows = []
    for item in reviews:
        rows.append({
            "local_only": True,
            "sample_id": item.sample_id,
            "reason": item.reason,
            "source_excerpts": [
                event.text[:240] for event in item.source_events
            ],
            "response_excerpts": [
                event.text[:240] for event in item.response_events
            ],
        })
    payload = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
        for row in rows
    )
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload, encoding="utf-8")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _validate_local_paths(args)
        salt = load_or_create_salt(args.id_salt_file)
        hasher = LocalIdHasher(salt)
        ingest = load_export(args.export_dir, args.target_uin)
        examples, extraction_reviews = extract_behavior_examples(
            ingest, hasher, args.target_alias
        )
        labeler = ReferenceLabeler(args.target_alias, args.target_uin)
        labels = {
            item.sample_id: labeler.label(item) for item in examples
        }
        if args.overrides:
            labels = apply_overrides(labels, load_overrides(args.overrides))
        label_reviews = collect_label_reviews(examples, labels)
        all_reviews = _dedupe_reviews(extraction_reviews + label_reviews)

        persona_registry = default_persona_registry()
        persona = persona_registry.resolve(
            persona_registry.current_persona_id,
            aliases=(args.current_alias,),
            relationships=(),
        )
        projector = ShadowProjector(
            BehaviorPolicy(),
            hasher,
            persona_context=persona,
            target_uin=args.target_uin,
            target_alias=args.target_alias,
            current_alias=args.current_alias,
        )
        projections = {
            item.sample_id: projector.project(item) for item in examples
        }
        high_confidence = {
            key: value
            for key, value in labels.items()
            if value.confidence is AssociationConfidence.HIGH
        }
        if not high_confidence:
            raise ValueError("no high-confidence alignment examples")
        report = build_diff_report(
            ingest.summary,
            examples,
            high_confidence,
            projections,
            review_count=len(all_reviews),
            configuration={
                "pipeline_version": "phase3-v1",
                "mechanics_version": "unified-participation-v1",
                "persona_id": persona.persona_id,
                "run_gap_ms": 15000,
                "adjacent_gap_ms": 20000,
                "directed_gap_ms": 60000,
                "context_window_ms": 30000,
            },
        )
        identifiers, raw_texts = _privacy_values(ingest, args)
        assert_shareable_report(report, identifiers, raw_texts)
        write_json_report(report, args.output)
        if args.markdown_output:
            write_markdown_report(report, args.markdown_output)
        if args.review_output:
            write_review_queue(all_reviews, args.review_output)
    except (
        ExportValidationError,
        PrivacyViolation,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        print("shadow export failed: {}".format(exc), file=sys.stderr)
        return 2
    print(json.dumps(report["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

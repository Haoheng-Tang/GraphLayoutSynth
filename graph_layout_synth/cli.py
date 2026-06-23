"""Command-line interface for GraphLayoutSynth."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from graph_layout_synth.archive import (
    ArchiveError,
    add_final_output_to_archive,
    build_archive_entry_from_selection,
    load_final_output_archive,
    load_selection_file,
    resolve_review_summary_from_selection,
)
from graph_layout_synth.config import DEFAULT_CONFIG_PATH, load_config
from graph_layout_synth.config_validator import export_config_validation_report, validate_config_file
from graph_layout_synth.diversity import (
    DEFAULT_LOW_NOVELTY_THRESHOLD,
    DEFAULT_NEAR_DUPLICATE_THRESHOLD,
    build_diversity_report,
    export_diversity_report_json,
)
from graph_layout_synth.export import (
    export_graph_json,
    export_ranking_report_csv,
    export_ranking_report_json,
    export_report_json,
    graph_report_data,
)
from graph_layout_synth.generator import generate_candidates
from graph_layout_synth.grammar_variant_assistant import (
    GrammarVariantError,
    build_grammar_variant_prompt,
    extract_rationale_from_llm_response,
    extract_yaml_from_llm_response,
    invalid_variant_path,
    load_variant_requirements,
    propose_grammar_variant_with_claude,
    room_mix_kwargs_from_requirements,
    validate_room_mix_targets,
    validate_variant_yaml_text,
    variant_requirements_to_design_intent,
    write_variant_outputs,
)
from graph_layout_synth.llm_evaluator import LlmEvaluationError, evaluate_candidates_with_llm
from graph_layout_synth.llm_evaluator import DEFAULT_CLAUDE_MODEL, load_llm_environment
from graph_layout_synth.ranking import rank_candidates
from graph_layout_synth.review_summary import (
    build_candidate_pool_summary,
    build_candidate_review_summary,
    export_review_summary_json,
)
from graph_layout_synth.tracing import export_trace_json, export_trace_summary, trace_metadata
from graph_layout_synth.visualize import visualize_graph


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(prog="graph_layout_synth")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Generate candidate layout graphs.")
    generate.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    generate.add_argument("--num-candidates", type=int, default=None)
    generate.add_argument("--top-k", type=int, default=1)
    generate.add_argument("--seed", type=int, default=None)
    generate.add_argument("--output-dir", type=Path, default=Path("outputs"))
    generate.add_argument("--archive-path", type=Path, default=None)
    generate.add_argument("--near-duplicate-threshold", type=float, default=DEFAULT_NEAR_DUPLICATE_THRESHOLD)
    generate.add_argument("--low-novelty-threshold", type=float, default=DEFAULT_LOW_NOVELTY_THRESHOLD)
    generate.add_argument(
        "--visualize",
        action="store_true",
        help="Save PNG visualizations for generated candidates.",
    )

    validate_config = subparsers.add_parser(
        "validate-config",
        help="Validate a YAML config without generating graphs.",
    )
    validate_config.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    validate_config.add_argument("--output", type=Path, default=None)

    evaluate_llm = subparsers.add_parser(
        "evaluate-llm",
        help="Use Claude to interpret deterministic ranking reports.",
    )
    evaluate_llm.add_argument("--ranking-report", type=Path, required=True)
    evaluate_llm.add_argument("--candidate-reports", nargs="*", default=[])
    evaluate_llm.add_argument("--output", type=Path, default=Path("outputs/llm_evaluation.md"))
    evaluate_llm.add_argument("--model", default="claude-3-5-haiku-latest")
    evaluate_llm.add_argument("--env-path", default=".env.local")
    evaluate_llm.add_argument("--max-tokens", type=int, default=1200)

    archive_final = subparsers.add_parser(
        "archive-final",
        help="Archive an explicitly selected final candidate.",
    )
    archive_final.add_argument("--selection", type=Path, default=None)
    archive_final.add_argument("--output-dir", type=Path, default=Path("outputs"))
    archive_final.add_argument("--archive-path", type=Path, default=None)
    archive_final.add_argument("--output-id", default=None)
    archive_final.add_argument("--allow-duplicate-output-id", action="store_true")
    archive_final.add_argument("--review-summary", type=Path, default=None)
    archive_final.add_argument("--notes", default=None)

    propose_variant = subparsers.add_parser(
        "propose-grammar-variant",
        help="Use Claude to propose a validated YAML grammar/config variant.",
    )
    propose_variant.add_argument("--base-config", type=Path, default=DEFAULT_CONFIG_PATH)
    propose_variant.add_argument("--variant-requirements", type=Path, default=None)
    propose_variant.add_argument("--design-intent", default=None)
    propose_variant.add_argument("--design-intent-file", type=Path, default=None)
    propose_variant.add_argument("--diversity-report", type=Path, default=None)
    propose_variant.add_argument("--review-summary", type=Path, default=None)
    propose_variant.add_argument("--archive-path", type=Path, default=None)
    propose_variant.add_argument("--output-config", type=Path, default=Path("outputs/llm_grammar_variant.yaml"))
    propose_variant.add_argument(
        "--rationale-output",
        type=Path,
        default=Path("outputs/llm_grammar_variant_rationale.md"),
    )
    propose_variant.add_argument("--raw-output", type=Path, default=Path("outputs/llm_grammar_variant_raw.md"))
    propose_variant.add_argument("--model", default=DEFAULT_CLAUDE_MODEL)
    propose_variant.add_argument("--max-tokens", type=int, default=4000)
    propose_variant.add_argument("--env-path", default=".env.local")
    propose_variant.add_argument("--write-prompt", type=Path, default=None)
    propose_variant.add_argument("--no-call", action="store_true")
    propose_variant.add_argument(
        "--require-room-mix-targets",
        action="store_true",
        help="Reject generated YAML unless it matches the default patient/support room-mix targets.",
    )
    propose_variant.add_argument("--patient-room-total-min", type=int, default=20)
    propose_variant.add_argument("--patient-room-total-max", type=int, default=30)
    propose_variant.add_argument("--clinical-support-ratio", type=float, default=0.25)
    propose_variant.add_argument("--staff-support-ratio", type=float, default=0.10)
    propose_variant.add_argument("--room-mix-ratio-tolerance", type=float, default=0.08)

    return parser


def run_generate(args: argparse.Namespace) -> None:
    """Generate candidates, export the best one, and print a short summary."""
    config = load_config(args.config)
    num_candidates = args.num_candidates or config.generation.num_candidates
    seed = args.seed if args.seed is not None else config.random_seed_default

    if num_candidates < 1:
        raise SystemExit("--num-candidates must be at least 1.")
    if args.top_k < 1:
        raise SystemExit("--top-k must be at least 1.")

    results = generate_candidates(num_candidates, seed, config, trace=True)
    candidate_trace_metadata = []
    candidate_artifact_paths = []
    for index, result in enumerate(results, start=1):
        trace_path = args.output_dir / f"candidate_{index}_trace.json"
        trace_summary_path = args.output_dir / f"candidate_{index}_trace.md"
        export_trace_json(result.trace, trace_path)
        export_trace_summary(result.trace, trace_summary_path)
        candidate_trace_metadata.append(trace_metadata(result.trace, trace_path))
        candidate_artifact_paths.append(
            {
                "graph_path": str(args.output_dir / f"candidate_{index}.json"),
                "report_path": str(args.output_dir / f"candidate_{index}_report.json"),
                "trace_path": str(trace_path),
                "image_path": str(args.output_dir / f"candidate_{index}.png") if args.visualize else None,
                "review_summary_path": str(args.output_dir / f"candidate_{index}_review_summary.json"),
            }
        )

    candidate_records = [
        {
            "candidate_id": f"candidate_{index}",
            "graph": result.graph,
            "validation_report": None,
            "trace_metadata": candidate_trace_metadata[index - 1],
            "export_paths": candidate_artifact_paths[index - 1],
        }
        for index, result in enumerate(results, start=1)
    ]
    ranked = rank_candidates(candidate_records, weights=config.ranking)
    ranked_by_id = {item["candidate_id"]: item for item in ranked}
    candidate_summaries = []
    for index, result in enumerate(results, start=1):
        candidate_id = f"candidate_{index}"
        ranking_entry = ranked_by_id[candidate_id]
        artifacts = candidate_artifact_paths[index - 1]
        report_metadata = {
            **candidate_trace_metadata[index - 1],
            "review_summary_path": artifacts["review_summary_path"],
        }
        export_graph_json(result.graph, artifacts["graph_path"])
        candidate_report = graph_report_data(
            result.graph,
            result.score,
            bool(ranking_entry["metrics"]["validation_passed"]),
            result.validation_errors,
            metrics=ranking_entry["metrics"],
            final_score=ranking_entry["final_score"],
            score_breakdown=ranking_entry["score_breakdown"],
            trace_metadata=report_metadata,
        )
        export_report_json(
            result.graph,
            artifacts["report_path"],
            result.score,
            bool(ranking_entry["metrics"]["validation_passed"]),
            result.validation_errors,
            metrics=ranking_entry["metrics"],
            final_score=ranking_entry["final_score"],
            score_breakdown=ranking_entry["score_breakdown"],
            trace_metadata=report_metadata,
        )
        if args.visualize:
            visualize_graph(
                result.graph,
                artifacts["image_path"],
                title=f"{candidate_id}: score {ranking_entry['final_score']:.1f}",
                config=config,
            )
        candidate_summary = build_candidate_review_summary(
            candidate_id,
            result.graph,
            candidate_report=candidate_report,
            ranking_entry=ranking_entry,
            artifact_paths=artifacts,
        )
        export_review_summary_json(candidate_summary, artifacts["review_summary_path"])
        candidate_summaries.append(candidate_summary)

    export_review_summary_json(
        {
            "pool_summary": build_candidate_pool_summary(candidate_summaries),
            "candidate_summaries": candidate_summaries,
        },
        args.output_dir / "review_summary.json",
    )
    archive_path = args.archive_path or args.output_dir / "final_output_archive.json"
    try:
        archive = load_final_output_archive(archive_path)
    except ArchiveError as exc:
        raise SystemExit(str(exc)) from exc
    diversity_report = build_diversity_report(
        candidate_summaries,
        archive=archive,
        near_duplicate_threshold=args.near_duplicate_threshold,
        low_novelty_threshold=args.low_novelty_threshold,
    )
    diversity_report["archive_path"] = str(archive_path)
    diversity_report["archive_used"] = archive_path.exists()
    export_diversity_report_json(diversity_report, args.output_dir / "diversity_report.json")
    top_k = ranked[: min(args.top_k, len(ranked))]
    best = ranked[0]
    best_result = results[int(best["candidate_id"].split("_")[-1]) - 1]
    best_trace_path = args.output_dir / "best_candidate_trace.json"
    best_trace_summary_path = args.output_dir / "best_candidate_trace.md"
    export_trace_json(best_result.trace, best_trace_path)
    export_trace_summary(best_result.trace, best_trace_summary_path)
    best_trace_metadata = trace_metadata(best_result.trace, best_trace_path)
    output_path = args.output_dir / "best_candidate.json"
    export_graph_json(best["graph"], output_path)
    report_path = args.output_dir / "best_candidate_report.json"
    export_report_json(
        best["graph"],
        report_path,
        best_result.score,
        bool(best["metrics"]["validation_passed"]),
        best_result.validation_errors,
        metrics=best["metrics"],
        final_score=best["final_score"],
        score_breakdown=best["score_breakdown"],
        trace_metadata={
            **best_trace_metadata,
            "review_summary_path": best["export_paths"].get("review_summary_path"),
        },
    )
    export_ranking_report_json(ranked, args.output_dir / "ranking_report.json")
    export_ranking_report_csv(ranked, args.output_dir / "ranking_report.csv")

    for item in top_k:
        candidate_index = int(item["candidate_id"].split("_")[-1])
        result = results[candidate_index - 1]
        prefix = f"top_{item['rank']}_{item['candidate_id']}"
        graph_path = args.output_dir / f"{prefix}.json"
        report_candidate_path = args.output_dir / f"{prefix}_report.json"
        top_trace_path = args.output_dir / f"{prefix}_trace.json"
        top_trace_summary_path = args.output_dir / f"{prefix}_trace.md"
        export_trace_json(result.trace, top_trace_path)
        export_trace_summary(result.trace, top_trace_summary_path)
        top_trace_metadata = trace_metadata(result.trace, top_trace_path)
        export_graph_json(item["graph"], graph_path)
        export_report_json(
            item["graph"],
            report_candidate_path,
            result.score,
            bool(item["metrics"]["validation_passed"]),
            result.validation_errors,
            metrics=item["metrics"],
            final_score=item["final_score"],
            score_breakdown=item["score_breakdown"],
            trace_metadata={
                **top_trace_metadata,
                "review_summary_path": item["export_paths"].get("review_summary_path"),
            },
        )

    if args.visualize:
        for item in top_k:
            visualize_graph(
                item["graph"],
                args.output_dir / f"top_{item['rank']}_{item['candidate_id']}.png",
                title=f"{item['candidate_id']}: score {item['final_score']:.1f}",
                config=config,
            )
        visualize_graph(
            best["graph"],
            args.output_dir / "best_candidate.png",
            title=f"Best candidate: score {best['final_score']:.1f}",
            config=config,
        )

    valid_count = sum(1 for result in results if result.is_valid)
    print(f"Config: {args.config}.")
    print(f"Generated {len(results)} candidate(s).")
    print(f"Valid candidates: {valid_count}.")
    print(f"Best final score: {best['final_score']:.1f}.")
    print(f"Best graph: {best['metrics']['node_count']} nodes, {best['metrics']['edge_count']} edges.")
    print(f"Saved best candidate to {output_path}.")
    print(f"Saved best report to {report_path}.")
    print("Top candidates:")
    for item in top_k:
        metrics = item["metrics"]
        print(
            f"  {item['candidate_id']}: score={item['final_score']:.1f}, "
            f"valid={metrics['validation_passed']}, rooms={metrics['room_count']}, "
            f"corridor_access={metrics['corridor_access_ratio']:.2f}"
        )
    if args.visualize:
        print(f"Saved top-k PNG visualizations to {args.output_dir}.")


def run_validate_config(args: argparse.Namespace) -> None:
    """Validate a YAML config and optionally export a validation report."""
    report = validate_config_file(args.config)
    if args.output:
        export_config_validation_report(report, args.output)

    if report.is_valid:
        print(f"Config is valid: {args.config}.")
        if args.output:
            print(f"Validation report: {args.output}.")
        return

    print(f"Config is invalid: {args.config}.")
    for error in report.errors:
        print(f"- {error}")
    if args.output:
        print(f"Validation report: {args.output}.")
    raise SystemExit(1)


def run_evaluate_llm(args: argparse.Namespace) -> None:
    """Run optional Claude interpretation over ranking reports."""
    try:
        result = evaluate_candidates_with_llm(
            ranking_report_path=str(args.ranking_report),
            candidate_report_paths=[str(path) for path in args.candidate_reports],
            model=args.model,
            output_path=str(args.output),
            env_path=args.env_path,
            max_tokens=args.max_tokens,
        )
    except LlmEvaluationError as exc:
        raise SystemExit(str(exc)) from exc

    print(f"Saved LLM evaluation to {result['output_path']}.")
    print(f"Model: {result['model']}.")


def run_archive_final(args: argparse.Namespace) -> None:
    """Archive an explicitly selected final candidate."""
    archive_path = args.archive_path or args.output_dir / "final_output_archive.json"
    try:
        if args.selection:
            selection = load_selection_file(args.selection)
            review_summary_path, review_summary = resolve_review_summary_from_selection(selection, args.output_dir)
            artifact_paths = review_summary.setdefault("artifact_paths", {})
            artifact_paths.setdefault("review_summary_path", str(review_summary_path))
        elif args.review_summary:
            review_summary = json.loads(args.review_summary.read_text(encoding="utf-8"))
            selection = {
                "selected_candidate_id": review_summary.get("candidate_id"),
                "selection_source": "manual",
                "selection_rationale": args.notes,
            }
            if not selection["selected_candidate_id"]:
                raise ArchiveError("Review summary is missing required field 'candidate_id'.")
            artifact_paths = review_summary.setdefault("artifact_paths", {})
            artifact_paths.setdefault("review_summary_path", str(args.review_summary))
        else:
            raise ArchiveError("Provide --selection or --review-summary.")

        entry = build_archive_entry_from_selection(selection, review_summary, output_id=args.output_id)
        archive = add_final_output_to_archive(
            archive_path,
            entry,
            allow_duplicate_output_id=args.allow_duplicate_output_id,
        )
    except (ArchiveError, FileNotFoundError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc

    print(f"Archived final output: {entry['output_id']}.")
    print(f"Candidate: {entry['candidate_id']}.")
    print(f"Archive: {archive_path}.")
    print(f"Archive size: {len(archive.get('outputs', []))}.")


def _read_yaml_mapping(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise GrammarVariantError(f"YAML file must contain a mapping: {path}")
    return data


def _read_optional_json(path: Path | None) -> dict | list[dict] | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _combined_design_intent(inline_intent: str | None, intent_file: Path | None) -> str | None:
    parts = []
    if intent_file:
        parts.append(intent_file.read_text(encoding="utf-8").strip())
    if inline_intent:
        parts.append(inline_intent.strip())
    return "\n\n".join(part for part in parts if part) or None


def _merged_design_intent(
    requirements_design_intent: str | None,
    inline_intent: str | None,
    intent_file: Path | None,
) -> str | None:
    parts = []
    if requirements_design_intent:
        parts.append(requirements_design_intent)
    combined_freeform = _combined_design_intent(inline_intent, intent_file)
    if combined_freeform:
        parts.append(combined_freeform)
    return "\n\n".join(parts) or None


def run_propose_grammar_variant(args: argparse.Namespace) -> None:
    """Use Claude to propose a validated YAML config variant."""
    try:
        base_config = _read_yaml_mapping(args.base_config)
        validate_variant_yaml_text(yaml.safe_dump(base_config, sort_keys=False))
        grammar_skills_text = Path("docs/GRAMMAR_CONFIG_SKILLS.md").read_text(encoding="utf-8")
        variant_requirements = load_variant_requirements(args.variant_requirements) if args.variant_requirements else None
        requirements_design_intent = variant_requirements_to_design_intent(variant_requirements)
        requirements_room_mix_kwargs = room_mix_kwargs_from_requirements(variant_requirements)
        prompt = build_grammar_variant_prompt(
            base_config,
            grammar_skills_text,
            design_intent=_merged_design_intent(
                requirements_design_intent,
                args.design_intent,
                args.design_intent_file,
            ),
            diversity_report=_read_optional_json(args.diversity_report),
            review_summary=_read_optional_json(args.review_summary),
            archive=_read_optional_json(args.archive_path),
        )
        if args.write_prompt:
            args.write_prompt.parent.mkdir(parents=True, exist_ok=True)
            args.write_prompt.write_text(prompt, encoding="utf-8")
        if args.no_call:
            if not args.write_prompt:
                raise GrammarVariantError("--no-call requires --write-prompt so the dry-run has an artifact.")
            print(f"Wrote grammar-variant prompt to {args.write_prompt}.")
            print("No Claude call was made.")
            return

        load_llm_environment(args.env_path)
        print(f"Calling Claude grammar variant assistant with model {args.model}.")
        print(f"Prompt length: {len(prompt)} characters.")
        response_text = propose_grammar_variant_with_claude(
            prompt,
            model=args.model,
            max_tokens=args.max_tokens,
        )
        print(f"Received Claude response: {len(response_text)} characters.")
        if args.raw_output:
            args.raw_output.parent.mkdir(parents=True, exist_ok=True)
            args.raw_output.write_text(response_text, encoding="utf-8")
            print(f"Saved raw Claude response to {args.raw_output}.")
        try:
            print("Extracting and validating YAML config variant.")
            yaml_text = extract_yaml_from_llm_response(response_text)
            raw_variant_config = validate_variant_yaml_text(yaml_text)
            if args.require_room_mix_targets or requirements_room_mix_kwargs:
                room_mix_kwargs = requirements_room_mix_kwargs or {
                    "patient_total_min": args.patient_room_total_min,
                    "patient_total_max": args.patient_room_total_max,
                    "clinical_ratio": args.clinical_support_ratio,
                    "staff_ratio": args.staff_support_ratio,
                    "ratio_tolerance": args.room_mix_ratio_tolerance,
                }
                room_mix_report = validate_room_mix_targets(
                    raw_variant_config,
                    **room_mix_kwargs,
                )
                print(
                    "Room-mix target check passed: "
                    f"{room_mix_report['estimated_totals']}."
                )
        except GrammarVariantError:
            invalid_path = invalid_variant_path(args.output_config)
            invalid_path.parent.mkdir(parents=True, exist_ok=True)
            if "yaml_text" in locals():
                invalid_path.write_text(yaml_text.rstrip() + "\n", encoding="utf-8")
                print(f"Saved invalid YAML to {invalid_path}.")
            if args.raw_output:
                print(f"Saved raw Claude response to {args.raw_output}.")
            raise

        rationale_text = extract_rationale_from_llm_response(response_text)
        write_variant_outputs(
            yaml_text,
            rationale_text,
            args.output_config,
            args.rationale_output,
        )
    except (GrammarVariantError, FileNotFoundError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise SystemExit(str(exc)) from exc

    print(f"Saved grammar config variant to {args.output_config}.")
    if args.rationale_output and rationale_text:
        print(f"Saved rationale to {args.rationale_output}.")
    print(f"Model: {args.model}.")


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "generate":
        run_generate(args)
    elif args.command == "validate-config":
        run_validate_config(args)
    elif args.command == "evaluate-llm":
        run_evaluate_llm(args)
    elif args.command == "archive-final":
        run_archive_final(args)
    elif args.command == "propose-grammar-variant":
        run_propose_grammar_variant(args)


if __name__ == "__main__":
    main()

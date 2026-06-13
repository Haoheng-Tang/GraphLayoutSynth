"""Command-line interface for GraphLayoutSynth."""

from __future__ import annotations

import argparse
from pathlib import Path

from graph_layout_synth.config import DEFAULT_CONFIG_PATH, load_config
from graph_layout_synth.export import (
    export_graph_json,
    export_ranking_report_csv,
    export_ranking_report_json,
    export_report_json,
)
from graph_layout_synth.generator import generate_candidates
from graph_layout_synth.llm_evaluator import LlmEvaluationError, evaluate_candidates_with_llm
from graph_layout_synth.ranking import rank_candidates
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
    generate.add_argument(
        "--visualize",
        action="store_true",
        help="Save PNG visualizations for generated candidates.",
    )

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

    results = generate_candidates(num_candidates, seed, config)
    candidate_records = [
        {
            "candidate_id": f"candidate_{index}",
            "graph": result.graph,
            "validation_report": None,
        }
        for index, result in enumerate(results, start=1)
    ]
    ranked = rank_candidates(candidate_records)
    top_k = ranked[: min(args.top_k, len(ranked))]
    best = ranked[0]
    output_path = args.output_dir / "best_candidate.json"
    export_graph_json(best["graph"], output_path)
    report_path = args.output_dir / "best_candidate_report.json"
    best_result = results[int(best["candidate_id"].split("_")[-1]) - 1]
    export_report_json(
        best["graph"],
        report_path,
        best_result.score,
        bool(best["metrics"]["validation_passed"]),
        best_result.validation_errors,
        metrics=best["metrics"],
        ranking_score=best["ranking_score"],
    )
    export_ranking_report_json(ranked, args.output_dir / "ranking_report.json")
    export_ranking_report_csv(ranked, args.output_dir / "ranking_report.csv")

    for item in top_k:
        candidate_index = int(item["candidate_id"].split("_")[-1])
        result = results[candidate_index - 1]
        prefix = f"top_{item['rank']}_{item['candidate_id']}"
        graph_path = args.output_dir / f"{prefix}.json"
        report_candidate_path = args.output_dir / f"{prefix}_report.json"
        export_graph_json(item["graph"], graph_path)
        export_report_json(
            item["graph"],
            report_candidate_path,
            result.score,
            bool(item["metrics"]["validation_passed"]),
            result.validation_errors,
            metrics=item["metrics"],
            ranking_score=item["ranking_score"],
        )

    if args.visualize:
        for item in top_k:
            visualize_graph(
                item["graph"],
                args.output_dir / f"top_{item['rank']}_{item['candidate_id']}.png",
                title=f"{item['candidate_id']}: score {item['ranking_score']:.1f}",
                config=config,
            )
        visualize_graph(
            best["graph"],
            args.output_dir / "best_candidate.png",
            title=f"Best candidate: score {best['ranking_score']:.1f}",
            config=config,
        )

    valid_count = sum(1 for result in results if result.is_valid)
    print(f"Config: {args.config}.")
    print(f"Generated {len(results)} candidate(s).")
    print(f"Valid candidates: {valid_count}.")
    print(f"Best ranking score: {best['ranking_score']:.1f}.")
    print(f"Best graph: {best['metrics']['node_count']} nodes, {best['metrics']['edge_count']} edges.")
    print(f"Saved best candidate to {output_path}.")
    print(f"Saved best report to {report_path}.")
    print("Top candidates:")
    for item in top_k:
        metrics = item["metrics"]
        print(
            f"  {item['candidate_id']}: score={item['ranking_score']:.1f}, "
            f"valid={metrics['validation_passed']}, rooms={metrics['room_count']}, "
            f"corridor_access={metrics['corridor_access_ratio']:.2f}"
        )
    if args.visualize:
        print(f"Saved top-k PNG visualizations to {args.output_dir}.")


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


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "generate":
        run_generate(args)
    elif args.command == "evaluate-llm":
        run_evaluate_llm(args)


if __name__ == "__main__":
    main()

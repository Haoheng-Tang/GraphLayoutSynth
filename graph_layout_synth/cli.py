"""Command-line interface for GraphLayoutSynth."""

from __future__ import annotations

import argparse
from pathlib import Path

from graph_layout_synth.export import export_graph_json, export_report_json
from graph_layout_synth.generator import generate_candidates, select_best_candidate
from graph_layout_synth.visualize import visualize_graph


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(prog="graph_layout_synth")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Generate candidate layout graphs.")
    generate.add_argument("--num-candidates", type=int, default=5)
    generate.add_argument("--seed", type=int, default=None)
    generate.add_argument("--output-dir", type=Path, default=Path("outputs"))
    generate.add_argument(
        "--visualize",
        action="store_true",
        help="Save PNG visualizations for generated candidates.",
    )

    return parser


def run_generate(args: argparse.Namespace) -> None:
    """Generate candidates, export the best one, and print a short summary."""
    if args.num_candidates < 1:
        raise SystemExit("--num-candidates must be at least 1.")

    results = generate_candidates(args.num_candidates, args.seed)
    best = select_best_candidate(results)
    output_path = args.output_dir / "best_candidate.json"
    export_graph_json(best.graph, output_path)
    report_path = args.output_dir / "best_candidate_report.json"
    export_report_json(
        best.graph,
        report_path,
        best.score,
        best.is_valid,
        best.validation_errors,
    )

    if args.visualize:
        for index, result in enumerate(results, start=1):
            visualize_graph(
                result.graph,
                args.output_dir / f"candidate_{index}.png",
                title=f"Candidate {index}: score {result.score:.1f}",
            )
        visualize_graph(
            best.graph,
            args.output_dir / "best_candidate.png",
            title=f"Best candidate: score {best.score:.1f}",
        )

    valid_count = sum(1 for result in results if result.is_valid)
    print(f"Generated {len(results)} candidate(s).")
    print(f"Valid candidates: {valid_count}.")
    print(f"Best score: {best.score:.1f}.")
    print(f"Best graph: {best.graph.number_of_nodes()} nodes, {best.graph.number_of_edges()} edges.")
    print(f"Saved best candidate to {output_path}.")
    print(f"Saved best report to {report_path}.")
    if args.visualize:
        print(f"Saved PNG visualizations to {args.output_dir}.")


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "generate":
        run_generate(args)


if __name__ == "__main__":
    main()

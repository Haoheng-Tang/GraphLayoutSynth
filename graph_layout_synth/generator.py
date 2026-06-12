"""Candidate generation orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from random import Random

import networkx as nx

from graph_layout_synth.config import LayoutConfig, load_config
from graph_layout_synth.grammar import complete_expansion, seed_graph
from graph_layout_synth.scoring import score_graph
from graph_layout_synth.validators import validate_graph


@dataclass(frozen=True)
class GenerationResult:
    """A generated graph with validation and scoring metadata."""

    graph: nx.Graph
    score: float
    is_valid: bool
    validation_errors: list[str]


def generate_candidate(
    seed: int | None = None,
    config: LayoutConfig | None = None,
) -> GenerationResult:
    """Generate one complete candidate graph.

    Passing a seed makes the stochastic choices deterministic.
    """
    config = config or load_config()
    if seed is None:
        seed = config.random_seed_default
    rng = Random(seed)
    graph = complete_expansion(seed_graph(config), rng, config)
    validation = validate_graph(graph, config)
    score = score_graph(graph, validation)
    return GenerationResult(
        graph=graph,
        score=score,
        is_valid=validation.is_valid,
        validation_errors=validation.errors,
    )


def generate_candidates(
    num_candidates: int,
    seed: int | None = None,
    config: LayoutConfig | None = None,
) -> list[GenerationResult]:
    """Generate multiple candidates using deterministic per-candidate seeds."""
    config = config or load_config()
    if seed is None:
        seed = config.random_seed_default
    rng = Random(seed)
    results = []
    for _ in range(num_candidates):
        candidate_seed = rng.randint(0, 2**32 - 1)
        results.append(generate_candidate(candidate_seed, config))
    return results


def select_best_candidate(results: list[GenerationResult]) -> GenerationResult:
    """Select the highest-scoring generated candidate."""
    if not results:
        raise ValueError("At least one candidate is required.")
    return max(results, key=lambda result: result.score)

"""Backend/internal generation constraint profiles.

These are procedural search/generation parameters (local group size bounds,
corridor hub/degree limits, relaxation limits). They are deliberately not part
of the user-facing `ProgramRequirements` schema: concepts like cluster count
or corridor degree have no stable user-facing semantic meaning yet. The
preflight validator consumes them to detect infeasible programs; future
generation algorithms may consume them directly.

Config-reachable room-count ranges are a separate concern and stay derived
from the active YAML config through `ConfigContract`; this profile describes
backend capacity independent of any one grammar config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


CONSTRAINT_PROFILE_SCHEMA_VERSION = 1
TOP_LEVEL_KEYS = {"schemaVersion", "locality", "corridors", "generation"}
LOCALITY_KEYS = {"patientRoomGroupSize", "localGroupCount"}
FLEXIBLE_BOUND_KEYS = {"min", "preferredMax", "hardMax"}
CORRIDOR_KEYS = {"avoidSingleHubCorridor", "corridorDegree", "allowCorridorChains"}
CORRIDOR_DEGREE_KEYS = {"preferredMax", "hardMax"}
GENERATION_KEYS = {"maxRelaxationSteps"}


class ConstraintProfileError(RuntimeError):
    """Raised when an internal constraint profile cannot be parsed."""


@dataclass(frozen=True)
class FlexibleBound:
    """Internal bound with a preferred maximum and a hard maximum."""

    min: int
    preferred_max: int
    hard_max: int

    def to_dict(self) -> dict[str, int]:
        return {"min": self.min, "preferredMax": self.preferred_max, "hardMax": self.hard_max}


@dataclass(frozen=True)
class CorridorDegreeBound:
    """Internal corridor connection-count bound."""

    preferred_max: int
    hard_max: int

    def to_dict(self) -> dict[str, int]:
        return {"preferredMax": self.preferred_max, "hardMax": self.hard_max}


@dataclass(frozen=True)
class LocalityConstraints:
    """Internal local grouping bounds for room generation."""

    patient_room_group_size: FlexibleBound = field(
        default_factory=lambda: FlexibleBound(min=4, preferred_max=8, hard_max=12)
    )
    local_group_count: FlexibleBound = field(
        default_factory=lambda: FlexibleBound(min=1, preferred_max=8, hard_max=20)
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "patientRoomGroupSize": self.patient_room_group_size.to_dict(),
            "localGroupCount": self.local_group_count.to_dict(),
        }


@dataclass(frozen=True)
class CorridorConstraints:
    """Internal corridor topology limits."""

    avoid_single_hub_corridor: bool = True
    corridor_degree: CorridorDegreeBound = field(
        default_factory=lambda: CorridorDegreeBound(preferred_max=8, hard_max=16)
    )
    allow_corridor_chains: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "avoidSingleHubCorridor": self.avoid_single_hub_corridor,
            "corridorDegree": self.corridor_degree.to_dict(),
            "allowCorridorChains": self.allow_corridor_chains,
        }


@dataclass(frozen=True)
class GenerationSearchConstraints:
    """Internal generation-search limits."""

    max_relaxation_steps: int = 3

    def to_dict(self) -> dict[str, Any]:
        return {"maxRelaxationSteps": self.max_relaxation_steps}


@dataclass(frozen=True)
class GenerationConstraintProfile:
    """Backend/internal constraint profile for preflight and future generation."""

    schema_version: int = CONSTRAINT_PROFILE_SCHEMA_VERSION
    locality: LocalityConstraints = field(default_factory=LocalityConstraints)
    corridors: CorridorConstraints = field(default_factory=CorridorConstraints)
    generation: GenerationSearchConstraints = field(default_factory=GenerationSearchConstraints)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "locality": self.locality.to_dict(),
            "corridors": self.corridors.to_dict(),
            "generation": self.generation.to_dict(),
        }


def default_constraint_profile() -> GenerationConstraintProfile:
    """Return the built-in default internal constraint profile."""
    return GenerationConstraintProfile()


def _require_mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConstraintProfileError(f"Constraint profile section '{path}' must be a mapping.")
    return value


def _reject_unknown_keys(data: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(str(key) for key in data if key not in allowed)
    if unknown:
        raise ConstraintProfileError(
            f"Constraint profile section '{path}' has unsupported field(s): {', '.join(unknown)}."
        )


def _positive_int(data: dict[str, Any], key: str, path: str, default: int, minimum: int = 0) -> int:
    value = data.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ConstraintProfileError(
            f"Constraint profile field '{path}.{key}' must be an integer >= {minimum}."
        )
    return value


def _boolean(data: dict[str, Any], key: str, path: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ConstraintProfileError(f"Constraint profile field '{path}.{key}' must be true or false.")
    return value


def _parse_flexible_bound(data: Any, path: str, default: FlexibleBound) -> FlexibleBound:
    if data is None:
        return default
    mapping = _require_mapping(data, path)
    _reject_unknown_keys(mapping, FLEXIBLE_BOUND_KEYS, path)
    bound = FlexibleBound(
        min=_positive_int(mapping, "min", path, default.min),
        preferred_max=_positive_int(mapping, "preferredMax", path, default.preferred_max, minimum=1),
        hard_max=_positive_int(mapping, "hardMax", path, default.hard_max, minimum=1),
    )
    if not bound.min <= bound.preferred_max <= bound.hard_max:
        raise ConstraintProfileError(
            f"Constraint profile section '{path}' must satisfy min <= preferredMax <= hardMax."
        )
    return bound


def _parse_corridor_degree(data: Any, path: str, default: CorridorDegreeBound) -> CorridorDegreeBound:
    if data is None:
        return default
    mapping = _require_mapping(data, path)
    _reject_unknown_keys(mapping, CORRIDOR_DEGREE_KEYS, path)
    bound = CorridorDegreeBound(
        preferred_max=_positive_int(mapping, "preferredMax", path, default.preferred_max, minimum=1),
        hard_max=_positive_int(mapping, "hardMax", path, default.hard_max, minimum=1),
    )
    if bound.preferred_max > bound.hard_max:
        raise ConstraintProfileError(f"Constraint profile section '{path}' must satisfy preferredMax <= hardMax.")
    return bound


def parse_constraint_profile(data: Any) -> GenerationConstraintProfile:
    """Parse a raw mapping into a profile, overlaying built-in defaults."""
    defaults = default_constraint_profile()
    if data is None:
        return defaults
    mapping = _require_mapping(data, "<root>")
    _reject_unknown_keys(mapping, TOP_LEVEL_KEYS, "<root>")
    schema_version = mapping.get("schemaVersion", CONSTRAINT_PROFILE_SCHEMA_VERSION)
    if schema_version != CONSTRAINT_PROFILE_SCHEMA_VERSION:
        raise ConstraintProfileError(
            f"Constraint profile field 'schemaVersion' must be {CONSTRAINT_PROFILE_SCHEMA_VERSION}."
        )

    locality_data = mapping.get("locality")
    locality = defaults.locality
    if locality_data is not None:
        locality_mapping = _require_mapping(locality_data, "locality")
        _reject_unknown_keys(locality_mapping, LOCALITY_KEYS, "locality")
        locality = LocalityConstraints(
            patient_room_group_size=_parse_flexible_bound(
                locality_mapping.get("patientRoomGroupSize"),
                "locality.patientRoomGroupSize",
                defaults.locality.patient_room_group_size,
            ),
            local_group_count=_parse_flexible_bound(
                locality_mapping.get("localGroupCount"),
                "locality.localGroupCount",
                defaults.locality.local_group_count,
            ),
        )

    corridors_data = mapping.get("corridors")
    corridors = defaults.corridors
    if corridors_data is not None:
        corridors_mapping = _require_mapping(corridors_data, "corridors")
        _reject_unknown_keys(corridors_mapping, CORRIDOR_KEYS, "corridors")
        corridors = CorridorConstraints(
            avoid_single_hub_corridor=_boolean(
                corridors_mapping, "avoidSingleHubCorridor", "corridors", defaults.corridors.avoid_single_hub_corridor
            ),
            corridor_degree=_parse_corridor_degree(
                corridors_mapping.get("corridorDegree"),
                "corridors.corridorDegree",
                defaults.corridors.corridor_degree,
            ),
            allow_corridor_chains=_boolean(
                corridors_mapping, "allowCorridorChains", "corridors", defaults.corridors.allow_corridor_chains
            ),
        )

    generation_data = mapping.get("generation")
    generation = defaults.generation
    if generation_data is not None:
        generation_mapping = _require_mapping(generation_data, "generation")
        _reject_unknown_keys(generation_mapping, GENERATION_KEYS, "generation")
        generation = GenerationSearchConstraints(
            max_relaxation_steps=_positive_int(
                generation_mapping, "maxRelaxationSteps", "generation", defaults.generation.max_relaxation_steps
            ),
        )

    return GenerationConstraintProfile(
        schema_version=CONSTRAINT_PROFILE_SCHEMA_VERSION,
        locality=locality,
        corridors=corridors,
        generation=generation,
    )


def load_constraint_profile(path: str | Path) -> GenerationConstraintProfile:
    """Load an internal constraint profile from a YAML or JSON file."""
    profile_path = Path(path)
    try:
        text = profile_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConstraintProfileError(f"Constraint profile file not found: {profile_path}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConstraintProfileError(f"Constraint profile file is not valid YAML: {profile_path}") from exc
    return parse_constraint_profile(data)

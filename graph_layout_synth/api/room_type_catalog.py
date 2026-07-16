"""Read-only room-type catalog derived from the active config vocabulary.

The catalog gives NextRoomPredictor a reliable dropdown source so the
frontend never hard-codes room type names. IDs are the canonical room types
used by GraphLayoutSynth configs and `ProgramRequirements` validation, taken
from the live `ConfigContract` (the `room_like` and `corridor` semantic
groups) so there is no second source of room-type truth. Abstract structural
node types such as `BuildingFloor` and `Zone` are not user-facing program
room types and are excluded.

Config resolution mirrors the `/suggest-next-room` sampler:
`GRAPHLAYOUTSYNTH_GRAMMAR_MODE` selects static/env-config/active-variant
behavior, with the same backward-compatible fallback when the mode is unset.
This module never calls the LLM, never generates graphs, and never modifies
variant state.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from graph_layout_synth.api.models import (
    ProgramRoomTypeCatalogItem,
    ProgramRoomTypeCatalogResponse,
)
from graph_layout_synth.api.sampling import (
    GRAMMAR_MODE_ACTIVE_VARIANT,
    GRAMMAR_MODE_ENV,
    GRAMMAR_MODE_ENV_CONFIG,
    GRAMMAR_MODE_STATIC,
    SUGGESTION_CONFIG_PATH_ENV,
)
from graph_layout_synth.config import DEFAULT_CONFIG_PATH
from graph_layout_synth.config_contract import build_config_contract
from graph_layout_synth.grammar_variant_control_plane import (
    GrammarVariantControlPlaneError,
    active_variant_config_path,
)
from graph_layout_synth.program_preflight import load_raw_config_mapping


CATALOG_SOURCE_DEFAULT = "default_config"
CATALOG_SOURCE_ENV = "env_config"
CATALOG_SOURCE_ACTIVE_VARIANT = "active_variant"
CATALOG_SOURCE_REQUEST = "request_config"

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


class RoomTypeCatalogError(RuntimeError):
    """Raised when the room-type catalog cannot be built."""


def resolve_catalog_config_path() -> tuple[str, Path]:
    """Return the catalog's config source label and path.

    Follows the same resolution as the suggestion sampler: explicit
    `GRAPHLAYOUTSYNTH_GRAMMAR_MODE`, or the backward-compatible fallback of
    `GRAPHLAYOUTSYNTH_SUGGESTION_CONFIG` when set, otherwise the default
    config. In active-variant mode a missing/invalid pointer fails explicitly
    rather than silently falling back.
    """
    mode = os.getenv(GRAMMAR_MODE_ENV, "").strip().lower()
    configured_path = os.getenv(SUGGESTION_CONFIG_PATH_ENV)
    if not mode:
        mode = GRAMMAR_MODE_ENV_CONFIG if configured_path else GRAMMAR_MODE_STATIC

    if mode == GRAMMAR_MODE_STATIC:
        return CATALOG_SOURCE_DEFAULT, Path(DEFAULT_CONFIG_PATH)
    if mode == GRAMMAR_MODE_ENV_CONFIG:
        if not configured_path:
            raise RoomTypeCatalogError(
                f"{SUGGESTION_CONFIG_PATH_ENV} must be set when "
                f"{GRAMMAR_MODE_ENV}=env_config."
            )
        return CATALOG_SOURCE_ENV, Path(configured_path).expanduser()
    if mode == GRAMMAR_MODE_ACTIVE_VARIANT:
        try:
            return CATALOG_SOURCE_ACTIVE_VARIANT, active_variant_config_path()
        except GrammarVariantControlPlaneError as exc:
            raise RoomTypeCatalogError(str(exc)) from exc
    raise RoomTypeCatalogError(
        f"Unsupported {GRAMMAR_MODE_ENV} '{mode}'. Expected static, "
        "env_config, or active_variant."
    )


def display_name_for_room_type(room_type: str) -> str:
    """Humanize a CamelCase room type ID, e.g. ``PatientRoom`` -> ``Patient room``."""
    words = _CAMEL_BOUNDARY.sub(" ", room_type).split()
    if not words:
        return room_type
    return " ".join(
        [words[0]]
        + [word if word.isupper() else word.lower() for word in words[1:]]
    )


def build_room_type_catalog(
    raw_config: dict[str, Any],
    *,
    source: str,
    config_path: str | Path,
) -> ProgramRoomTypeCatalogResponse:
    """Build the deterministic catalog from one raw config mapping."""
    contract = build_config_contract(raw_config)
    if contract.errors:
        raise RoomTypeCatalogError(
            "Config is invalid, so no room-type catalog can be derived: "
            + "; ".join(contract.errors)
        )
    room_types = sorted(
        set(contract.room_like_node_types) | set(contract.corridor_node_types)
    )
    return ProgramRoomTypeCatalogResponse(
        room_types=[
            ProgramRoomTypeCatalogItem(
                id=room_type,
                display_name=display_name_for_room_type(room_type),
            )
            for room_type in room_types
        ],
        source=source,
        config_path=str(config_path),
    )


def room_type_catalog_response(
    base_config_path: str | None = None,
) -> ProgramRoomTypeCatalogResponse:
    """Return the catalog for an explicit config path or the active config."""
    if base_config_path:
        source = CATALOG_SOURCE_REQUEST
        config_path: Path = Path(base_config_path).expanduser()
    else:
        source, config_path = resolve_catalog_config_path()
    raw_config = load_raw_config_mapping(config_path)
    return build_room_type_catalog(raw_config, source=source, config_path=config_path)

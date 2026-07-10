"""Validated request and response models for the NextRoomPredictor API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ApiModel(BaseModel):
    """Base model using the frontend's camel-case field names."""

    model_config = ConfigDict(
        alias_generator=lambda name: "".join(
            word if index == 0 else word.capitalize()
            for index, word in enumerate(name.split("_"))
        ),
        populate_by_name=True,
        extra="ignore",
    )


class Room(ApiModel):
    """One rectangular room from the frontend floorplan."""

    id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    rotation: float | None = None


class DoorOrAdjacency(ApiModel):
    """One door or wall relationship between frontend rooms."""

    id: str = Field(min_length=1)
    source_room_id: str = Field(min_length=1)
    target_room_id: str = Field(min_length=1)
    side: str | None = None
    edge_type: Literal["wall", "door"]


class FloorplanState(ApiModel):
    """Current NextRoomPredictor state supplied at prediction time."""

    schema_version: Literal[1]
    rooms: list[Room] = Field(min_length=1)
    edges: list[DoorOrAdjacency] = Field(default_factory=list)
    selected_room_id: str | None = None

    @model_validator(mode="after")
    def validate_references(self) -> "FloorplanState":
        room_ids = [room.id for room in self.rooms]
        if len(room_ids) != len(set(room_ids)):
            raise ValueError("Room IDs must be unique.")

        room_id_set = set(room_ids)
        for edge in self.edges:
            if edge.source_room_id not in room_id_set:
                raise ValueError(
                    f"Edge '{edge.id}' references unknown sourceRoomId "
                    f"'{edge.source_room_id}'."
                )
            if edge.target_room_id not in room_id_set:
                raise ValueError(
                    f"Edge '{edge.id}' references unknown targetRoomId "
                    f"'{edge.target_room_id}'."
                )
            if edge.source_room_id == edge.target_room_id:
                raise ValueError(f"Edge '{edge.id}' must connect two different rooms.")

        if self.selected_room_id is not None and self.selected_room_id not in room_id_set:
            raise ValueError(
                f"selectedRoomId '{self.selected_room_id}' does not exist in floorplan.rooms."
            )
        return self


class SuggestNextRoomRequest(ApiModel):
    """Request for semantic next-room-type suggestions."""

    floorplan: FloorplanState
    anchor_room_id: str = Field(min_length=1)
    sample_count: int = Field(ge=1, le=200, strict=True)
    include_debug_artifacts: bool = Field(default=False, strict=True)
    include_debug_visualizations: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def validate_anchor(self) -> "SuggestNextRoomRequest":
        if self.anchor_room_id not in {room.id for room in self.floorplan.rooms}:
            raise ValueError(
                f"anchorRoomId '{self.anchor_room_id}' does not exist in floorplan.rooms."
            )
        return self


class NextRoomTypeSuggestion(ApiModel):
    """Aggregated evidence for one possible new neighbor room type."""

    room_type: str
    sample_count: int = Field(ge=0)
    sample_share: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    reason: str | None = None


class SuggestNextRoomResponse(ApiModel):
    """Ranked next-room suggestions returned to NextRoomPredictor."""

    suggestions: list[NextRoomTypeSuggestion]
    sample_count: int = Field(ge=0)
    predictor_version: str


class GrammarVariantProposeRequest(ApiModel):
    """Request for proposing or dry-running a YAML grammar/config variant."""

    heuristic_instructions: str = Field(min_length=1)
    base_config_path: str | None = None
    variant_requirements: dict[str, Any] | None = None
    program_requirements: dict[str, Any] | None = None
    constraint_profile: dict[str, Any] | None = None
    activate_if_valid: bool = Field(default=False, strict=True)
    dry_run: bool = Field(default=False, strict=True)
    model: str | None = None


class ProgramRequirementsValidateRequest(ApiModel):
    """Request for deterministic program-requirements preflight validation."""

    program_requirements: dict[str, Any]
    base_config_path: str | None = None
    constraint_profile: dict[str, Any] | None = None

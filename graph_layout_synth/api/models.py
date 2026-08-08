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


class SuggestedIntendedEdge(ApiModel):
    """One aggregated secondary edge from the suggested room to an existing room.

    ``edge_type`` here is the relationship between the *suggested new room*
    and an existing frontend room; the anchor relationship stays in the parent
    suggestion's ``edge_type``. ``target_existing_room_id`` is omitted when
    several existing rooms share the same room type and anchor edge type, so
    the generated evidence cannot name one of them unambiguously.
    """

    target_existing_room_id: str | None = None
    target_room_type: str
    edge_type: Literal["door", "wall"]
    edge_type_counts: dict[Literal["door", "wall"], int] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    sample_count: int | None = Field(default=None, ge=0)


class NextRoomTypeSuggestion(ApiModel):
    """Aggregated evidence for one possible new neighbor room type."""

    room_type: str
    sample_count: int = Field(ge=0)
    sample_share: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    reason: str | None = None
    edge_type: Literal["door", "wall"] | None = None
    edge_type_counts: dict[Literal["door", "wall"], int] | None = None
    intended_edges: list[SuggestedIntendedEdge] | None = None


class SuggestionConfigSourceInfo(ApiModel):
    """Which config the sampler actually used for one suggestion request.

    Reported so callers can confirm which grammar produced a suggestion
    without enabling a debug artifact run. `variantId` is set only in
    active-variant mode.
    """

    mode: str
    config_path: str | None = None
    variant_id: str | None = None


class SuggestNextRoomResponse(ApiModel):
    """Ranked next-room suggestions returned to NextRoomPredictor.

    `matchedSampleCount`, `samplesWithCandidates`, and `configSource` are
    additive diagnostics populated on every response, including empty ones —
    that is the case they exist for. They separate "no generated graph
    contained a semantic anchor match" (`matchedSampleCount == 0`) from "the
    grammar considers this anchor saturated" (`matchedSampleCount > 0` with
    `samplesWithCandidates == 0`), and name the config behind the result.
    Diagnostics only: do not drive application logic from them.
    """

    suggestions: list[NextRoomTypeSuggestion]
    sample_count: int = Field(ge=0)
    predictor_version: str
    matched_sample_count: int = Field(default=0, ge=0)
    samples_with_candidates: int = Field(default=0, ge=0)
    config_source: SuggestionConfigSourceInfo | None = None


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


MAX_INSTRUCTION_VARIANT_REPAIR_ATTEMPTS = 3
MAX_INSTRUCTION_VARIANT_SAMPLES = 25


class InstructionVariantProposeRequest(ApiModel):
    """Request to translate free-form design instructions into a config variant.

    Claude is called only when this request is submitted with non-empty
    ``instructionText`` and ``dryRun`` is not set; it is never invoked for
    program-requirement validation, room-type catalog lookups, variant
    listing/inspection/activation, or `/suggest-next-room`.
    """

    instruction_text: str = Field(min_length=1)
    name: str | None = None
    base_config_path: str | None = None
    repair_attempts: int = Field(default=0, ge=0, le=MAX_INSTRUCTION_VARIANT_REPAIR_ATTEMPTS, strict=True)
    samples: int = Field(default=0, ge=0, le=MAX_INSTRUCTION_VARIANT_SAMPLES, strict=True)
    dry_run: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def validate_instruction_text(self) -> "InstructionVariantProposeRequest":
        if not self.instruction_text.strip():
            raise ValueError("instructionText must be non-empty after trimming whitespace.")
        return self


class InstructionVariantAttemptSummary(ApiModel):
    """One initial or repair attempt's outcome, for the HTTP response."""

    attempt_index: int
    kind: Literal["initial", "repair"]
    valid: bool
    validation_error_count: int
    artifact_dir: str | None = None


class InstructionVariantProposeResponse(ApiModel):
    """Outcome of one instruction-guided config-variant proposal request."""

    status: Literal["dry_run", "proposed_valid", "generated", "proposed_invalid", "failed"]
    variant_id: str | None = None
    valid: bool
    repair_attempts_used: int
    generation_ran: bool
    artifact_dir: str
    attempts: list[InstructionVariantAttemptSummary] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    generated_samples_png_dir: str | None = None


class ProgramRoomTypeCatalogItem(ApiModel):
    """One canonical user-facing room type from the active config vocabulary.

    ``generated``/``tier`` tell the frontend which types the active grammar
    actually creates: `generated` types can appear in `/suggest-next-room`
    results; `optional` types are valid vocabulary (Program Requirements,
    variant proposals) but produce no suggestions until a config variant
    generates them.

    ``groups`` lists every semantic group the active config assigns this type
    to, verbatim and sorted (for example `["corridor", "room_like"]`). Group
    names are config-defined rather than a fixed enum, so clients must treat
    unknown names as opaque. This is the declared source for role-based
    frontend behavior such as corridor auto-extension and default room depth;
    matching substrings in the type ID is not.

    All three fields are additive; existing clients ignore them.
    """

    id: str = Field(min_length=1)
    display_name: str | None = None
    description: str | None = None
    generated: bool = False
    tier: Literal["generated", "optional"] = "optional"
    groups: list[str] = Field(default_factory=list)


class ProgramRoomTypeCatalogResponse(ApiModel):
    """Deterministic, read-only room-type catalog for frontend dropdowns."""

    room_types: list[ProgramRoomTypeCatalogItem]
    source: str | None = None
    config_path: str | None = None

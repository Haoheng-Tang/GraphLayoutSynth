"""FastAPI application for NextRoomPredictor integration."""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from graph_layout_synth.api.models import (
    GrammarVariantProposeRequest,
    InstructionVariantProposeRequest,
    InstructionVariantProposeResponse,
    ProgramRequirementsValidateRequest,
    ProgramRoomTypeCatalogResponse,
    SuggestNextRoomRequest,
    SuggestNextRoomResponse,
)
from graph_layout_synth.api.predictor import NextRoomPredictor
from graph_layout_synth.api.room_type_catalog import (
    RoomTypeCatalogError,
    room_type_catalog_response,
)
from graph_layout_synth.config import DEFAULT_CONFIG_PATH
from graph_layout_synth.generation_constraint_profile import ConstraintProfileError, parse_constraint_profile
from graph_layout_synth.grammar_variant_control_plane import (
    GrammarVariantControlPlaneError,
    activate_variant,
    list_variant_records,
    llm_variant_control_plane_enabled,
    propose_variant_from_instructions,
    variant_detail,
)
from graph_layout_synth.instruction_variant_control_plane import (
    propose_instruction_variant_from_request,
)
from graph_layout_synth.llm_evaluator import load_llm_environment
from graph_layout_synth.program_preflight import load_raw_config_mapping, run_program_preflight
from graph_layout_synth.program_requirements import ProgramRequirementsError


LOGGER = logging.getLogger(__name__)

# Load `.env.local` into the process environment once, at import time, so
# local development does not require exporting GRAPHLAYOUTSYNTH_*/
# ANTHROPIC_API_KEY into the shell before starting uvicorn -- matching the
# CLI commands, which already default to `--env-path .env.local`.
# `load_llm_environment` never overrides a variable already present in
# `os.environ` (see graph_layout_synth/llm_evaluator.py), and this runs only
# once per process, before `create_app()` is ever called. Tests remain
# isolated: `tests/conftest.py`'s autouse fixture clears every
# GraphLayoutSynth service env var before each test, which happens after
# this module-level load has already run during test collection.
load_llm_environment()
DEFAULT_ALLOWED_ORIGIN = "http://localhost:5173"


def _allowed_origins() -> list[str]:
    configured = os.getenv("NEXT_ROOM_ALLOWED_ORIGINS", "")
    return list(
        dict.fromkeys(
            [
                DEFAULT_ALLOWED_ORIGIN,
                *(origin.strip() for origin in configured.split(",") if origin.strip()),
            ]
        )
    )


def create_app(predictor: NextRoomPredictor | None = None) -> FastAPI:
    """Build the API application, with an injectable predictor for tests."""
    app = FastAPI(title="GraphLayoutSynth Next Room API", version="1.0.0")
    app.state.predictor = predictor or NextRoomPredictor()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"detail": jsonable_encoder(exc.errors())},
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/suggest-next-room",
        response_model=SuggestNextRoomResponse,
        response_model_by_alias=True,
        response_model_exclude_none=True,
    )
    def suggest_next_room(
        request: SuggestNextRoomRequest,
    ) -> SuggestNextRoomResponse:
        try:
            return app.state.predictor.suggest(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            LOGGER.exception("Next-room generation failed.")
            raise HTTPException(
                status_code=500,
                detail="Next-room prediction failed.",
            ) from exc

    @app.get(
        "/program-requirements/room-types",
        response_model=ProgramRoomTypeCatalogResponse,
        response_model_by_alias=True,
    )
    def program_room_type_catalog(
        base_config_path: str | None = Query(default=None, alias="baseConfigPath"),
    ) -> ProgramRoomTypeCatalogResponse:
        """Read-only canonical room-type catalog; no LLM, no generation."""
        try:
            return room_type_catalog_response(base_config_path)
        except (RoomTypeCatalogError, ProgramRequirementsError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/program-requirements/validate")
    def validate_program_requirements(
        request: ProgramRequirementsValidateRequest,
    ) -> dict:
        """Deterministic preflight validation; never calls the LLM or generates graphs."""
        try:
            profile = parse_constraint_profile(request.constraint_profile)
            raw_config = load_raw_config_mapping(request.base_config_path or DEFAULT_CONFIG_PATH)
            result = run_program_preflight(
                request.program_requirements,
                raw_config=raw_config,
                profile=profile,
            )
        except (ProgramRequirementsError, ConstraintProfileError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result.to_dict()

    def _require_llm_variant_control_plane_enabled() -> None:
        if not llm_variant_control_plane_enabled():
            raise HTTPException(
                status_code=403,
                detail=(
                    "LLM grammar variant endpoints are disabled. Set "
                    "GRAPHLAYOUTSYNTH_ENABLE_LLM_VARIANTS=true to enable them."
                ),
            )

    @app.post("/grammar-variants/propose")
    def propose_grammar_variant(
        request: GrammarVariantProposeRequest,
    ) -> dict:
        _require_llm_variant_control_plane_enabled()
        try:
            return propose_variant_from_instructions(
                heuristic_instructions=request.heuristic_instructions,
                base_config_path=request.base_config_path or DEFAULT_CONFIG_PATH,
                variant_requirements=request.variant_requirements,
                program_requirements=request.program_requirements,
                constraint_profile=request.constraint_profile,
                model=request.model,
                dry_run=request.dry_run,
                activate_if_valid=request.activate_if_valid,
            )
        except GrammarVariantControlPlaneError as exc:
            detail: dict[str, object] = {"message": str(exc)}
            if exc.record is not None:
                detail["variant"] = exc.record
            raise HTTPException(status_code=exc.status_code, detail=detail) from exc

    @app.post(
        "/grammar-variants/propose-from-instructions",
        response_model=InstructionVariantProposeResponse,
        response_model_by_alias=True,
    )
    def propose_grammar_variant_from_instructions(
        request: InstructionVariantProposeRequest,
    ) -> InstructionVariantProposeResponse:
        """Translate submitted design instructions into a grammar/config variant.

        Gated the same way as every other `/grammar-variants/*` endpoint,
        including dry runs, matching this control plane's existing
        convention. Claude is called only when this endpoint is reached with
        `dryRun=false`; `/suggest-next-room`, program-requirement validation,
        the room-type catalog, and variant listing/inspection/activation
        never call it.
        """
        _require_llm_variant_control_plane_enabled()
        try:
            return propose_instruction_variant_from_request(request)
        except GrammarVariantControlPlaneError as exc:
            detail: dict[str, object] = {"message": str(exc)}
            if exc.record is not None:
                detail["variant"] = exc.record
            raise HTTPException(status_code=exc.status_code, detail=detail) from exc

    @app.get("/grammar-variants")
    def list_grammar_variants() -> dict[str, list[dict]]:
        _require_llm_variant_control_plane_enabled()
        try:
            return {"variants": list_variant_records()}
        except GrammarVariantControlPlaneError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @app.get("/grammar-variants/{variant_id}")
    def inspect_grammar_variant(variant_id: str) -> dict:
        _require_llm_variant_control_plane_enabled()
        try:
            return variant_detail(variant_id)
        except GrammarVariantControlPlaneError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @app.post("/grammar-variants/{variant_id}/activate")
    def activate_grammar_variant(variant_id: str) -> dict:
        _require_llm_variant_control_plane_enabled()
        try:
            return {"variant": activate_variant(variant_id)}
        except GrammarVariantControlPlaneError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    return app


app = create_app()

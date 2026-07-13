"""FastAPI application for NextRoomPredictor integration."""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from graph_layout_synth.api.models import (
    GrammarVariantProposeRequest,
    ProgramRequirementsValidateRequest,
    SuggestNextRoomRequest,
    SuggestNextRoomResponse,
)
from graph_layout_synth.api.predictor import NextRoomPredictor
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
from graph_layout_synth.program_preflight import load_raw_config_mapping, run_program_preflight
from graph_layout_synth.program_requirements import ProgramRequirementsError


LOGGER = logging.getLogger(__name__)
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

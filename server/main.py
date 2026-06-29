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
    SuggestNextRoomRequest,
    SuggestNextRoomResponse,
)
from graph_layout_synth.api.predictor import NextRoomPredictor


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

    return app


app = create_app()

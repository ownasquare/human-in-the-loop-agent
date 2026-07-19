"""Stable FastAPI error projection."""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from relay.persistence import ConflictError, NotFoundError, StaleApprovalError


def _response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {
                "loc": list(item.get("loc", ())),
                "type": str(item.get("type", "validation_error")),
                "msg": str(item.get("msg", "Invalid value.")),
            }
            for item in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed.",
                    "details": details,
                }
            },
        )

    @app.exception_handler(NotFoundError)
    async def not_found(_request: Request, exc: NotFoundError) -> JSONResponse:
        return _response(404, "not_found", str(exc))

    @app.exception_handler(StaleApprovalError)
    async def stale(_request: Request, exc: StaleApprovalError) -> JSONResponse:
        return _response(409, "stale_approval", str(exc))

    @app.exception_handler(ConflictError)
    async def conflict(_request: Request, exc: ConflictError) -> JSONResponse:
        return _response(409, "conflict", str(exc))

    @app.exception_handler(ValueError)
    async def invalid(_request: Request, _exc: ValueError) -> JSONResponse:
        return _response(422, "invalid_request", "Request could not be processed.")

    @app.exception_handler(Exception)
    async def internal(_request: Request, _exc: Exception) -> JSONResponse:
        request_id = f"req_{uuid.uuid4().hex}"
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "Relay could not complete the request.",
                    "request_id": request_id,
                }
            },
            headers={"X-Request-ID": request_id},
        )

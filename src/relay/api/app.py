"""FastAPI application factory and production entry point."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from relay.api.errors import install_error_handlers
from relay.api.routes import router
from relay.config import Settings, get_settings
from relay.runtime import RelayRuntime, open_runtime


def create_app(
    settings: Settings | None = None,
    *,
    runtime: RelayRuntime | None = None,
) -> FastAPI:
    active_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if runtime is not None:
            app.state.runtime = runtime
            yield
            return
        async with open_runtime(active_settings) as active_runtime:
            app.state.runtime = active_runtime
            yield

    app = FastAPI(
        title="Relay Human-in-the-Loop Agent",
        version="0.1.0",
        description="Approval-first durable operations workflow API.",
        lifespan=lifespan,
    )
    install_error_handlers(app)
    app.include_router(router)

    static_dir = active_settings.static_dir
    if static_dir is not None:
        root = Path(static_dir).resolve()
        index = root / "index.html"
        assets = root / "assets"
        if index.is_file():
            if assets.is_dir():
                app.mount("/assets", StaticFiles(directory=assets), name="assets")

            @app.get("/{spa_path:path}", include_in_schema=False)
            async def spa_fallback(spa_path: str) -> Response:
                if spa_path == "api" or spa_path.startswith("api/"):
                    return JSONResponse(
                        status_code=404,
                        content={
                            "error": {
                                "code": "not_found",
                                "message": "API route not found.",
                            }
                        },
                    )
                return FileResponse(index)

    return app


app = create_app()


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run("relay.api.app:app", host=settings.host, port=settings.port, reload=False)

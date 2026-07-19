"""Relay operator CLI."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from relay.config import get_settings
from relay.runtime import open_runtime

app = typer.Typer(help="Operate the Relay human-in-the-loop agent.", no_args_is_help=True)
demo_app = typer.Typer(help="Manage deterministic demo data.")
app.add_typer(demo_app, name="demo")
console = Console()


@app.command()
def doctor() -> None:
    """Report readiness without displaying any credential value."""

    settings = get_settings()

    async def inspect() -> dict[str, Any]:
        async with open_runtime(settings) as runtime:
            return runtime.readiness()

    result = asyncio.run(inspect())
    table = Table(title="Relay readiness")
    table.add_column("Surface")
    table.add_column("Ready")
    table.add_column("Detail")
    table.add_row("Mode", "yes", str(result["mode"]))
    for connector in result["connectors"]:
        table.add_row(
            str(connector["name"]),
            "yes" if connector["ready"] else "no",
            str(connector["detail"]),
        )
    console.print(table)
    if not result["ready"]:
        raise typer.Exit(code=1)


@app.command()
def serve(
    host: str | None = typer.Option(default=None, help="Bind host; defaults to configuration."),
    port: int | None = typer.Option(default=None, min=1, max=65535),
    reload: bool = typer.Option(
        False,
        "--reload/--no-reload",
        help="Reload the development server when source files change.",
    ),
) -> None:
    """Serve the API and, when configured, the built frontend."""

    import uvicorn

    settings = get_settings()
    if host is not None:
        settings.host = host
    if port is not None:
        settings.port = port
    settings.assert_safe_bind()
    target = "relay.api.app:create_app" if reload else "relay.api.app:app"
    uvicorn.run(
        target,
        host=settings.host,
        port=settings.port,
        reload=reload,
        factory=reload,
    )


@demo_app.command("reset")
def demo_reset() -> None:
    """Restore deterministic fixtures and clear local workflow records."""

    settings = get_settings()
    if settings.mode != "demo":
        raise typer.BadParameter("Demo reset is unavailable in live mode.")

    async def reset() -> dict[str, object]:
        async with open_runtime(settings) as runtime:
            return await runtime.reset_demo()

    console.print_json(json.dumps(asyncio.run(reset())))


@app.command("graph")
def graph_command() -> None:
    """Print the workflow as Mermaid text."""

    settings = get_settings()

    async def render() -> str:
        async with open_runtime(settings) as runtime:
            return runtime.graph_mermaid()

    console.print(asyncio.run(render()))


if __name__ == "__main__":
    app()

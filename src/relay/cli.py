"""Relay operator CLI."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from relay.config import get_settings
from relay.live_acceptance import (
    LiveAcceptanceError,
    LiveAcceptanceResult,
    run_claude_acceptance,
    run_google_read_acceptance,
    run_google_write_acceptance,
    run_resend_acceptance,
    run_tavily_acceptance,
)
from relay.runtime import open_runtime

app = typer.Typer(help="Operate the Relay human-in-the-loop agent.", no_args_is_help=True)
demo_app = typer.Typer(help="Manage deterministic demo data.")
live_app = typer.Typer(help="Run one explicitly authorized live connector check at a time.")
app.add_typer(demo_app, name="demo")
app.add_typer(live_app, name="live")
console = Console()


def _render_readiness(
    connectors: list[dict[str, Any]], *, title: str, mode: str | None = None
) -> None:
    table = Table(title=title)
    table.add_column("Surface")
    table.add_column("Ready")
    table.add_column("Detail")
    if mode is not None:
        table.add_row("Mode", "yes", mode)
    for connector in connectors:
        table.add_row(
            str(connector["name"]),
            "yes" if connector["ready"] else "no",
            str(connector["detail"]),
        )
    console.print(table)


def _run_live_check(
    operation: Coroutine[Any, Any, LiveAcceptanceResult],
) -> None:
    try:
        result = asyncio.run(operation)
    except LiveAcceptanceError as exc:
        raise typer.BadParameter(str(exc)) from None
    console.print_json(result.model_dump_json())


@app.command()
def doctor() -> None:
    """Report readiness without displaying any credential value."""

    settings = get_settings()

    async def inspect() -> dict[str, Any]:
        async with open_runtime(settings) as runtime:
            return runtime.readiness()

    result = asyncio.run(inspect())
    _render_readiness(result["connectors"], title="Relay readiness", mode=str(result["mode"]))
    if not result["ready"]:
        raise typer.Exit(code=1)


@live_app.command("doctor")
def live_doctor() -> None:
    """Report connector configuration without calling a provider."""

    rows = [dict(item) for item in get_settings().live_connector_readiness()]
    _render_readiness(rows, title="Relay live preflight")
    required = [item for item in rows if item["name"] != "purchasing"]
    if not all(bool(item["ready"]) for item in required):
        raise typer.Exit(code=1)


@live_app.command("claude")
def live_claude(
    confirm: str = typer.Option(..., "--confirm", help="Exact Claude acknowledgement phrase."),
) -> None:
    """Run one read-only Claude structured-planning check."""

    _run_live_check(run_claude_acceptance(get_settings(), confirmation=confirm))


@live_app.command("tavily")
def live_tavily(
    confirm: str = typer.Option(..., "--confirm", help="Exact Tavily acknowledgement phrase."),
) -> None:
    """Run one bounded public Tavily search."""

    _run_live_check(run_tavily_acceptance(get_settings(), confirmation=confirm))


@live_app.command("resend")
def live_resend(
    confirm: str = typer.Option(..., "--confirm", help="Exact Resend acknowledgement phrase."),
    run_id: str = typer.Option(..., "--run-id", help="Unique acceptance identity."),
) -> None:
    """Send one controlled email and verify readback plus idempotent replay."""

    _run_live_check(run_resend_acceptance(get_settings(), confirmation=confirm, run_id=run_id))


@live_app.command("google-read")
def live_google_read(
    confirm: str = typer.Option(..., "--confirm", help="Exact Google read acknowledgement."),
    start: str = typer.Option(..., "--start", help="Offset-aware ISO 8601 interval start."),
    end: str = typer.Option(..., "--end", help="Offset-aware ISO 8601 interval end."),
) -> None:
    """Read a bounded free/busy interval from the configured test calendar."""

    _run_live_check(
        run_google_read_acceptance(get_settings(), confirmation=confirm, start=start, end=end)
    )


@live_app.command("google-write")
def live_google_write(
    confirm: str = typer.Option(..., "--confirm", help="Exact Google write acknowledgement."),
    run_id: str = typer.Option(..., "--run-id", help="Unique acceptance identity."),
    start: str = typer.Option(..., "--start", help="Offset-aware ISO 8601 event start."),
    end: str = typer.Option(..., "--end", help="Offset-aware ISO 8601 event end."),
) -> None:
    """Create, read back, and idempotently reconcile one controlled test event."""

    _run_live_check(
        run_google_write_acceptance(
            get_settings(),
            confirmation=confirm,
            run_id=run_id,
            start=start,
            end=end,
        )
    )


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

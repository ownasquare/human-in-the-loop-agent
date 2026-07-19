from __future__ import annotations

import uvicorn
from typer.testing import CliRunner

from relay import cli


def test_doctor_graph_and_demo_reset_commands(settings, monkeypatch) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    runner = CliRunner()
    doctor = runner.invoke(cli.app, ["doctor"])
    assert doctor.exit_code == 0
    assert "Relay readiness" in doctor.stdout
    graph = runner.invoke(cli.app, ["graph"])
    assert graph.exit_code == 0
    assert "flowchart" in graph.stdout
    reset = runner.invoke(cli.app, ["demo", "reset"])
    assert reset.exit_code == 0
    assert '"status": "reset"' in reset.stdout


def test_serve_passes_reload_and_safe_bind_to_uvicorn(settings, monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    def fake_run(target: str, **kwargs: object) -> None:
        calls.append({"target": target, **kwargs})

    monkeypatch.setattr(uvicorn, "run", fake_run)
    result = CliRunner().invoke(cli.app, ["serve", "--reload"])
    assert result.exit_code == 0
    assert calls == [
        {
            "target": "relay.api.app:create_app",
            "host": "127.0.0.1",
            "port": 8000,
            "reload": True,
            "factory": True,
        }
    ]

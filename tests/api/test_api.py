from __future__ import annotations

import hashlib
import json
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from relay.api.app import create_app
from relay.models import AuditActor, Mode, utc_now
from relay.runtime import open_runtime


async def test_versioned_api_contract_and_stale_conflict(settings) -> None:
    async with open_runtime(settings) as runtime:
        app = create_app(settings, runtime=runtime)
        async with app.router.lifespan_context(app):  # noqa: SIM117 - readable lifespan scope
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                health = await client.get("/api/v1/health")
                assert health.status_code == 200
                created = await client.post(
                    "/api/v1/runs",
                    json={"instruction": "Prepare the Acme renewal workflow"},
                )
                assert created.status_code == 202
                body = created.json()
                assert set(body) == {
                    "run",
                    "steps",
                    "pending_approval",
                    "audit_events",
                    "receipts",
                }
                run_id = body["run"]["id"]
                proposal = body["pending_approval"]
                assert proposal["run_id"] == run_id
                assert proposal["step_id"] == body["steps"][3]["id"]
                assert set(proposal["allowed_decisions"]) == {
                    "approve",
                    "reject",
                    "revise",
                }
                assert proposal["action"]["send_updates"] == "none"
                run_detail = await client.get(f"/api/v1/runs/{run_id}")
                assert run_detail.status_code == 200
                approvals = await client.get("/api/v1/approvals")
                assert approvals.json()["total"] == 1
                stale = await client.post(
                    f"/api/v1/approvals/{proposal['id']}/decisions",
                    json={
                        "decision": "approve",
                        "expected_version": proposal["version"],
                        "expected_payload_hash": "0" * 64,
                        "reason": "",
                        "idempotency_key": "api-stale-decision",
                    },
                )
                assert stale.status_code == 409
                assert stale.json()["error"]["code"] == "stale_approval"
                audit = await client.get("/api/v1/audit-events", params={"run_id": run_id})
                assert audit.status_code == 200
                assert audit.json()["total"] >= 1
                connectors = await client.get("/api/v1/connectors")
                assert connectors.json()["total"] == 6


async def test_run_idempotency_conflict_is_exposed_by_api(settings) -> None:
    async with open_runtime(settings) as runtime:
        app = create_app(settings, runtime=runtime)
        async with app.router.lifespan_context(app):  # noqa: SIM117 - readable lifespan scope
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                first = await client.post(
                    "/api/v1/runs",
                    json={
                        "instruction": "First workflow request",
                        "idempotency_key": "api-exact-run-key",
                    },
                )
                assert first.status_code == 202
                conflict = await client.post(
                    "/api/v1/runs",
                    json={
                        "instruction": "Different workflow request",
                        "idempotency_key": "api-exact-run-key",
                    },
                )
                assert conflict.status_code == 409
                assert conflict.json()["error"]["code"] == "conflict"


async def test_demo_reset_is_available_only_in_demo(settings) -> None:
    async with open_runtime(settings) as runtime:
        app = create_app(settings, runtime=runtime)
        async with app.router.lifespan_context(app):  # noqa: SIM117 - readable lifespan scope
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/api/v1/demo/reset")
                assert response.status_code == 200
                assert response.json()["counts"]["customers"] == 1


async def test_validation_and_internal_errors_are_stable_and_sanitized(settings) -> None:
    async with open_runtime(settings) as runtime:
        app = create_app(settings, runtime=runtime)

        async def explode() -> None:
            raise RuntimeError("sensitive internal detail")

        app.add_api_route("/api/v1/_test-error", explode, methods=["GET"])
        async with app.router.lifespan_context(app):  # noqa: SIM117 - readable lifespan scope
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                invalid = await client.post("/api/v1/runs", json={"instruction": "ZX"})
                assert invalid.status_code == 422
                invalid_body = invalid.json()
                assert invalid_body["error"]["code"] == "validation_error"
                assert "input" not in invalid.text
                assert "ZX" not in invalid.text
                failed = await client.get("/api/v1/_test-error")
                assert failed.status_code == 500
                assert failed.json()["error"]["code"] == "internal_error"
                assert failed.json()["error"]["message"] == (
                    "Relay could not complete the request."
                )
                assert "sensitive internal detail" not in failed.text
                assert failed.headers["x-request-id"] == failed.json()["error"]["request_id"]


async def test_malformed_revision_is_sanitized_and_leaves_proposal_pending(settings) -> None:
    sentinel = "PRIVATE-REVISION-SENTINEL-719"
    async with open_runtime(settings) as runtime:
        app = create_app(settings, runtime=runtime)
        async with app.router.lifespan_context(app):  # noqa: SIM117 - readable lifespan scope
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                created = await client.post(
                    "/api/v1/runs",
                    json={"instruction": "Prepare the Acme renewal workflow"},
                )
                proposal = created.json()["pending_approval"]
                malformed = proposal["action"]
                malformed["summary"] = {"private": sentinel}
                response = await client.post(
                    f"/api/v1/approvals/{proposal['id']}/decisions",
                    json={
                        "decision": "revise",
                        "expected_version": proposal["version"],
                        "expected_payload_hash": proposal["payload_hash"],
                        "reason": "",
                        "revised_action": malformed,
                        "idempotency_key": "malformed-revision-request",
                    },
                )
                assert response.status_code == 422
                assert response.json() == {
                    "error": {
                        "code": "invalid_request",
                        "message": "Request could not be processed.",
                    }
                }
                assert sentinel not in response.text
                assert runtime.repository.get_proposal(proposal["id"]).status.value == "pending"


async def test_spa_fallback_never_masks_unknown_api_routes(settings, tmp_path: Path) -> None:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html>Relay SPA</html>", encoding="utf-8")
    settings.static_dir = static_dir
    async with open_runtime(settings) as runtime:
        app = create_app(settings, runtime=runtime)
        async with app.router.lifespan_context(app):  # noqa: SIM117 - readable lifespan scope
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                api_missing = await client.get("/api/v1/does-not-exist")
                assert api_missing.status_code == 404
                assert api_missing.json()["error"]["code"] == "not_found"
                spa = await client.get("/work-queue")
                assert spa.status_code == 200
                assert "Relay SPA" in spa.text


async def test_audit_export_is_complete_beyond_ten_thousand_events(settings) -> None:
    async with open_runtime(settings) as runtime:
        run = runtime.repository.create_run(
            instruction="Export every audit event",
            mode=Mode.DEMO,
        )
        created_at = utc_now().isoformat()
        with runtime.repository._transaction() as connection:
            previous = connection.execute(
                "SELECT event_hash FROM audit_events WHERE run_id = ? "
                "ORDER BY sequence DESC LIMIT 1",
                (run.id,),
            ).fetchone()["event_hash"]
            rows: list[tuple[str, str, None, str, str, str, str, str, str]] = []
            for index in range(10_000):
                event_id = f"export_event_{index}"
                material = json.dumps(
                    {
                        "event_id": event_id,
                        "run_id": run.id,
                        "proposal_id": None,
                        "actor": AuditActor.SYSTEM.value,
                        "event_type": "export.test",
                        "detail": {},
                        "previous_hash": previous,
                        "created_at": created_at,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                event_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
                rows.append(
                    (
                        event_id,
                        run.id,
                        None,
                        AuditActor.SYSTEM.value,
                        "export.test",
                        "{}",
                        previous,
                        event_hash,
                        created_at,
                    )
                )
                previous = event_hash
            connection.executemany(
                """INSERT INTO audit_events
                (event_id, run_id, proposal_id, actor, event_type, detail_json, previous_hash,
                 event_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )

        app = create_app(settings, runtime=runtime)
        async with app.router.lifespan_context(app):  # noqa: SIM117 - readable lifespan scope
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/v1/audit-events/export",
                    params={"run_id": run.id},
                )

        body = response.json()
        assert response.status_code == 200
        assert body["total"] == 10_001
        assert len(body["items"]) == body["total"]
        assert body["chain_valid"] is True

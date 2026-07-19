import re
from pathlib import Path
from typing import Any

import yaml

WORKFLOW_PATH = Path(".github/workflows/release.yml")
EXPECTED_ACTIONS = {
    "actions/checkout": ("9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0", "v7"),
    "actions/setup-python": ("ece7cb06caefa5fff74198d8649806c4678c61a1", "v6"),
    "astral-sh/setup-uv": ("11f9893b081a58869d3b5fccaea48c9e9e46f990", "v8.3.2"),
    "actions/upload-artifact": ("043fb46d1a93c77aae656e7c1c64a875d1fc6a0a", "v7"),
    "actions/download-artifact": ("3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c", "v8"),
    "pypa/gh-action-pypi-publish": (
        "cef221092ed1bacb1cc03d23a2d87d1d172e277b",
        "release/v1",
    ),
}
BUILD_REF_GUARD = (
    "(github.event_name == 'release' && "
    "github.event.action == 'published' && "
    "github.ref == format('refs/tags/{0}', github.event.release.tag_name) && "
    "github.ref_type == 'tag' && "
    "github.ref_protected == true) || "
    "(github.event_name == 'workflow_dispatch' && "
    "github.ref == 'refs/heads/main' && "
    "github.ref_protected == true && "
    "github.workflow_ref == "
    "'ownasquare/human-in-the-loop-agent/.github/workflows/release.yml@refs/heads/main')"
)


def _load_workflow() -> tuple[str, dict[str, Any]]:
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = yaml.load(workflow_text, Loader=yaml.BaseLoader)  # noqa: S506
    assert isinstance(workflow, dict)
    return workflow_text, workflow


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    return next(step for step in job["steps"] if step.get("name") == name)


def test_release_triggers_manual_guard_permissions_and_action_pins() -> None:
    workflow_text, workflow = _load_workflow()

    assert workflow["on"] == {
        "release": {"types": ["published"]},
        "workflow_dispatch": {
            "inputs": {
                "tag": {
                    "description": "Exact existing release tag to publish, such as v0.1.0",
                    "required": "true",
                    "type": "string",
                }
            }
        },
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == {
        "group": "relay-pypi-${{ github.event.release.tag_name || inputs.tag }}",
        "cancel-in-progress": "false",
    }

    jobs = workflow["jobs"]
    assert set(jobs) == {"build", "publish"}
    build = jobs["build"]
    assert build["permissions"] == {"contents": "read"}
    assert " ".join(build["if"].split()) == BUILD_REF_GUARD

    uses_values = [step["uses"] for job in jobs.values() for step in job["steps"] if "uses" in step]
    assert len(uses_values) == len(EXPECTED_ACTIONS)
    parsed_actions: dict[str, str] = {}
    for uses_value in uses_values:
        match = re.fullmatch(r"([^@]+)@([0-9a-f]{40})", uses_value)
        assert match is not None
        parsed_actions[match.group(1)] = match.group(2)

    assert parsed_actions == {action: sha for action, (sha, _version) in EXPECTED_ACTIONS.items()}
    for action, (sha, version) in EXPECTED_ACTIONS.items():
        assert f"uses: {action}@{sha} # {version}" in workflow_text

    for job in jobs.values():
        for step in job["steps"]:
            if run_body := step.get("run"):
                assert "${{" not in run_body


def test_build_validates_release_provenance_and_runs_full_backend_gates() -> None:
    _workflow_text, workflow = _load_workflow()
    build = workflow["jobs"]["build"]

    release_step = _step(build, "Verify published GitHub release")
    assert release_step["env"] == {
        "GITHUB_TOKEN": "${{ github.token }}",
        "GITHUB_API_URL": "${{ github.api_url }}",
        "GITHUB_REPOSITORY": "${{ github.repository }}",
    }
    assert 'os.environ["GITHUB_TOKEN"]' in release_step["run"]
    assert "/releases/tags/" in release_step["run"]
    assert 'release.get("draft") is not False' in release_step["run"]
    assert 'release.get("published_at")' in release_step["run"]

    checkout = next(
        step for step in build["steps"] if step.get("uses", "").startswith("actions/checkout@")
    )
    assert checkout["with"] == {
        "ref": "refs/tags/${{ env.RELEASE_TAG }}",
        "fetch-depth": "0",
        "persist-credentials": "false",
    }

    provenance = _step(build, "Verify exact tag and package version")["run"]
    assert '["git", "cat-file", "-t", tag_ref]' in provenance
    assert 'tag_type != "tag"' in provenance
    assert '["git", "merge-base", "--is-ancestor", tag_commit, "origin/main"]' in provenance
    assert 'expected_tag = f"v{project_version}"' in provenance

    expected_gates = {
        "Verify lockfile": "uv lock --check",
        "Lint backend": "uv run ruff check src tests",
        "Check backend formatting": "uv run ruff format --check src tests",
        "Type check backend": "uv run mypy src",
        "Test backend with branch coverage": (
            "uv run pytest --cov=relay --cov-branch --cov-report=term-missing"
        ),
        "Scan backend source": "uv run bandit -q -r src",
        "Audit Python dependencies": "uv run pip-audit",
        "Clean distribution directory": "rm -rf dist",
        "Build wheel and source distribution": (
            "uv build --build-constraint .release-build-constraints.txt"
        ),
        "Remove non-distribution build marker": "rm -f dist/.gitignore",
    }
    for step_name, expected_run in expected_gates.items():
        assert _step(build, step_name)["run"].strip() == expected_run

    build_backend_pin = _step(build, "Pin release build backend")["run"]
    assert '"hatchling==1.31.0\\n"' in build_backend_pin
    assert 'Path(".release-build-constraints.txt")' in build_backend_pin


def test_build_accepts_only_exact_expected_distribution_pair() -> None:
    _workflow_text, workflow = _load_workflow()
    build = workflow["jobs"]["build"]
    inspection = _step(build, "Inspect distribution identity")["run"]

    required_checks = (
        "if len(files) != 2 or len(wheels) != 1 or len(sdists) != 1:",
        "expected_wheel =",
        "expected_sdist =",
        "parse_wheel_filename(wheel.name)",
        "parse_sdist_filename(sdist.name)",
        '.endswith(".dist-info/METADATA")',
        '.endswith("/PKG-INFO")',
        "Metadata.from_email(raw_metadata, validate=True)",
        "canonicalize_name(metadata.name)",
        "metadata.version != expected_version",
    )
    for required_check in required_checks:
        assert required_check in inspection

    upload = next(
        step
        for step in build["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert upload["with"] == {
        "name": "python-package-distributions",
        "path": "dist/*",
        "if-no-files-found": "error",
        "retention-days": "7",
    }

    smoke = _step(build, "Smoke install wheel and source distribution")["run"]
    required_smokes = (
        "for artifact in (wheel, sdist):",
        "TemporaryDirectory",
        '"export",\n                "--frozen",\n                "--no-dev",',
        '"--no-emit-project",',
        "stdout=subprocess.DEVNULL",
        '"sync",\n                "--require-hashes",',
        '["uv", "venv", "--python", "3.12"',
        '"pip",\n                "install",\n                "--no-cache",',
        '"--no-deps",',
        '"--build-constraint",',
        '["uv", "pip", "check", "--python", str(smoke_python)]',
        '[str(smoke_python), "-I", "-c", import_check]',
        "import importlib.metadata, relay",
        '[str(relay_cli), "--help"]',
        '[str(relay_cli), "doctor"]',
        '[str(relay_cli), "demo", "reset"]',
        'print(f"Validated isolated package smoke: {artifact.name}")',
    )
    for required_smoke in required_smokes:
        assert required_smoke in smoke

    step_names = [step.get("name") for step in build["steps"]]
    assert (
        step_names.index("Inspect distribution identity")
        < step_names.index("Smoke install wheel and source distribution")
        < next(
            index
            for index, step in enumerate(build["steps"])
            if step.get("uses", "").startswith("actions/upload-artifact@")
        )
    )


def test_publish_job_is_two_step_oidc_only_artifact_handoff() -> None:
    workflow_text, workflow = _load_workflow()
    publish = workflow["jobs"]["publish"]

    assert set(publish) == {
        "name",
        "needs",
        "runs-on",
        "environment",
        "permissions",
        "steps",
    }
    assert publish["needs"] == "build"
    assert publish["environment"] == "pypi"
    assert publish["permissions"] == {"id-token": "write"}
    assert publish["steps"] == [
        {
            "uses": ("actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"),
            "with": {"name": "python-package-distributions", "path": "dist/"},
        },
        {"uses": ("pypa/gh-action-pypi-publish@cef221092ed1bacb1cc03d23a2d87d1d172e277b")},
    ]
    assert all("run" not in step for step in publish["steps"])
    assert "actions/checkout" not in str(publish)
    assert "password:" not in workflow_text
    assert "api-token" not in workflow_text.lower()
    assert "PYPI_TOKEN" not in workflow_text

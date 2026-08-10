from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[1]


def _load_yaml(relative_path: str) -> dict:
    return yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))


def test_dependabot_batches_routine_updates_and_bounds_open_pull_requests() -> None:
    config = _load_yaml(".github/dependabot.yml")
    updates = {entry["package-ecosystem"]: entry for entry in config["updates"]}

    assert set(updates) == {"pip", "npm", "docker", "github-actions"}
    for entry in updates.values():
        assert entry["schedule"]["interval"] == "monthly"
        assert entry["schedule"]["timezone"] == "Asia/Shanghai"
        assert entry["open-pull-requests-limit"] <= 2
        assert entry["rebase-strategy"] == "disabled"
        assert entry["groups"], "Routine updates must be grouped instead of creating one PR per dependency."

    assert updates["pip"]["groups"]["python-minor-patch"]["update-types"] == ["minor", "patch"]
    assert updates["npm"]["groups"]["frontend-runtime-minor-patch"]["dependency-type"] == "production"
    assert updates["npm"]["groups"]["frontend-tooling-minor-patch"]["dependency-type"] == "development"
    assert updates["github-actions"]["groups"]["github-actions-minor-patch"]["update-types"] == [
        "minor",
        "patch",
    ]
    assert updates["docker"]["groups"]["docker-minor-patch"]["update-types"] == ["minor", "patch"]


def test_dependabot_routes_major_version_work_to_the_operator_process() -> None:
    config = _load_yaml(".github/dependabot.yml")

    for entry in config["updates"]:
        ignored_types = {
            update_type
            for rule in entry.get("ignore", [])
            if rule.get("dependency-name") == "*"
            for update_type in rule.get("update-types", [])
        }
        assert "version-update:semver-major" in ignored_types

    policy = (ROOT / "docs" / "DEPENDENCY_MAINTENANCE.md").read_text(encoding="utf-8")
    assert "Security updates" in policy
    assert "Major upgrades" in policy
    assert "one major dependency at a time" in policy
    assert "Dependabot alerts" in policy

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "](docs/DEPENDENCY_MAINTENANCE.md)" in readme


def test_ci_runs_once_per_change_and_cancels_superseded_work() -> None:
    workflow = _load_yaml(".github/workflows/ci.yml")
    triggers = workflow.get("on", workflow.get(True))

    assert triggers["push"]["branches"] == ["main"]
    assert triggers["pull_request"]["branches"] == ["main"]
    assert workflow["concurrency"]["cancel-in-progress"] is True
    assert "github.event.pull_request.number" in workflow["concurrency"]["group"]


def test_ci_uses_native_change_classification_without_weakening_critical_suites() -> None:
    workflow = _load_yaml(".github/workflows/ci.yml")
    jobs = workflow["jobs"]

    assert "classify-changes" in jobs
    assert jobs["classify-changes"]["outputs"] == {
        "backend": "${{ steps.changes.outputs.backend }}",
        "frontend": "${{ steps.changes.outputs.frontend }}",
        "full": "${{ steps.changes.outputs.full }}",
    }

    for job_name in ("backend-tests", "backend-coverage", "backend-quality"):
        assert jobs[job_name]["needs"] == "classify-changes"
        assert "needs.classify-changes.outputs.backend" in jobs[job_name]["if"]

    assert jobs["frontend"]["needs"] == "classify-changes"
    assert "needs.classify-changes.outputs.frontend" in jobs["frontend"]["if"]

    for job_name in ("golden-demo-e2e", "container-security"):
        assert jobs[job_name]["needs"] == "classify-changes"
        assert "needs.classify-changes.outputs.full" in jobs[job_name]["if"]

    backend_matrix = jobs["backend-tests"]["strategy"]["matrix"]["include"]
    suites = {entry["suite"] for entry in backend_matrix}
    assert {"postgres-integration", "security"}.issubset(suites)


def test_ci_exposes_one_stable_required_gate_for_branch_protection() -> None:
    workflow = _load_yaml(".github/workflows/ci.yml")
    gate = workflow["jobs"]["required-ci-gate"]

    assert gate["name"] == "required-ci-gate"
    assert gate["if"] == "always()"
    assert set(gate["needs"]) == {
        "classify-changes",
        "backend-tests",
        "backend-coverage",
        "backend-quality",
        "frontend",
        "golden-demo-e2e",
        "container-security",
        "secret-scan",
    }
    rendered_steps = "\n".join(str(step) for step in gate["steps"])
    assert '"success", "skipped"' in rendered_steps
    assert "raise SystemExit" in rendered_steps

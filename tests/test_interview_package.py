from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_interview_package_contains_all_required_reviewable_materials() -> None:
    required = (
        "docs/interview/INTERVIEW_PACKAGE_INDEX.md",
        "docs/interview/ONE_PAGE_PROJECT_OVERVIEW.md",
        "docs/interview/THREAT_MODEL.md",
        "docs/interview/API_AND_DEMO_SCOPE.md",
        "docs/interview/INCIDENT_01_SCHEMA_REPAIR_LATENCY.md",
        "docs/interview/INCIDENT_02_APPROVAL_EXECUTION_BINDING.md",
        "docs/interview/CONTRIBUTION_MATRIX.md",
        "docs/adr/ADR-002-bounded-advisors-over-free-groupchat.md",
        "docs/adr/ADR-003-deterministic-business-calculations.md",
        "docs/adr/ADR-004-read-only-amazon-pilot.md",
    )

    for relative_path in required:
        path = ROOT / relative_path
        assert path.is_file(), relative_path
        assert len(path.read_text(encoding="utf-8")) > 500, relative_path


def test_interview_index_links_versioned_evidence_and_external_gaps() -> None:
    index = (ROOT / "docs/interview/INTERVIEW_PACKAGE_INDEX.md").read_text(encoding="utf-8")

    for reference in (
        "ONE_PAGE_PROJECT_OVERVIEW.md",
        "THREAT_MODEL.md",
        "API_AND_DEMO_SCOPE.md",
        "INCIDENT_01_SCHEMA_REPAIR_LATENCY.md",
        "INCIDENT_02_APPROVAL_EXECUTION_BINDING.md",
        "CONTRIBUTION_MATRIX.md",
        "../evidence/agent-evals/EVALUATION_REPORT.md",
        "../INTERVIEW_DEMO_RUNBOOK.md",
    ):
        assert reference in index
    assert "真实 Amazon" in index
    assert "真实模型" in index
    assert "PostgreSQL" in index


def test_threat_model_has_formal_security_analysis_sections() -> None:
    threat_model = (ROOT / "docs/interview/THREAT_MODEL.md").read_text(encoding="utf-8")

    for heading in ("## 资产", "## 攻击者", "## 信任边界", "## 威胁矩阵", "## 残余风险", "## 验证计划"):
        assert heading in threat_model
    for threat in (
        "Prompt injection",
        "跨租户",
        "审批篡改",
        "重放",
        "PII",
        "供应链",
    ):
        assert threat in threat_model


def test_core_adrs_use_a_consistent_decision_record_structure() -> None:
    for name in (
        "ADR-002-bounded-advisors-over-free-groupchat.md",
        "ADR-003-deterministic-business-calculations.md",
        "ADR-004-read-only-amazon-pilot.md",
    ):
        adr = (ROOT / "docs/adr" / name).read_text(encoding="utf-8")
        for heading in ("## Context", "## Decision", "## Alternatives", "## Consequences"):
            assert heading in adr, name


def test_contribution_matrix_does_not_claim_unverifiable_authorship() -> None:
    contribution = (ROOT / "docs/interview/CONTRIBUTION_MATRIX.md").read_text(encoding="utf-8")

    assert "paulrose4" in contribution
    assert "Ava" in contribution
    assert "候选人需确认" in contribution
    assert "AI 辅助" in contribution
    assert "不能从 Git 自动证明" in contribution

from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPOSITORY_ROOT / "README.md"


def test_readme_describes_the_current_product_and_engineering_contract() -> None:
    readme = README_PATH.read_text(encoding="utf-8")

    required_sections = (
        "## What OmniSell Does",
        "## Golden Workflow",
        "## Architecture",
        "## Tech Stack",
        "## Safety Model",
        "## Quick Start",
        "## Verification",
        "## Project Structure",
        "## Documentation",
    )
    for section in required_sections:
        assert section in readme, f"README is missing required section: {section}"

    required_product_claims = (
        "Amazon operations control tower",
        "not a free-chat product",
        "LLM output is advisory",
        "Decimal",
        "PostgreSQL RLS",
        "read_only_pilot",
    )
    for claim in required_product_claims:
        assert claim in readme, f"README is missing product boundary: {claim}"


def test_readme_core_documentation_links_resolve() -> None:
    readme = README_PATH.read_text(encoding="utf-8")
    required_links = (
        "docs/LOCAL_DEVELOPMENT.md",
        "docs/AGENT_RUNTIME.md",
        "docs/RUNBOOK.md",
        "docs/security/THREAT_MODEL.md",
        "docs/interview/ONE_PAGE_PROJECT_OVERVIEW.md",
    )

    for relative_path in required_links:
        assert f"]({relative_path})" in readme
        assert (REPOSITORY_ROOT / relative_path).is_file()

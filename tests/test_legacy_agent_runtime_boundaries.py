from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import httpx
import pytest


def test_missing_legacy_autogen_never_silently_creates_local_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    from legacy.autogen_demo import base

    monkeypatch.setattr(base, "AssistantAgent", None)

    with pytest.raises(RuntimeError, match="Legacy AutoGen runtime is unavailable"):
        base.create_assistant_agent(
            name="UnsafeFallbackProbe",
            system_message="This must not become a simulated production agent.",
            llm_config=False,
        )


def test_api_startup_does_not_initialize_legacy_group_chat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from api import main
    from api.config import Settings

    def fail_if_called(*args: object, **kwargs: object) -> object:
        raise AssertionError("Legacy GroupChat must not be initialized by production API startup.")

    monkeypatch.setattr(main, "create_omnisell_team", fail_if_called, raising=False)
    app = main.create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'no-legacy-team.sqlite'}",
            auto_create_schema=True,
            api_key="test-key",
            auth_mode="api_key",
        )
    )

    async def scenario() -> None:
        for handler in app.router.on_startup:
            result = handler()
            if hasattr(result, "__await__"):
                await result
        try:
            assert not hasattr(app.state, "team")
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                health = await client.get("/api/admin/health")
            assert health.status_code == 200
            payload = health.json()
            assert "team_ready" not in payload
            assert payload["advisor_runtime_ready"] is True
            assert payload["advisor_count"] == 4
        finally:
            for handler in app.router.on_shutdown:
                result = handler()
                if hasattr(result, "__await__"):
                    await result

    asyncio.run(scenario())


def test_runtime_requirements_do_not_install_legacy_autogen_proxy() -> None:
    requirements = Path("requirements.runtime.txt").read_text(encoding="utf-8").splitlines()

    assert not any(line.strip().lower().startswith("pyautogen") for line in requirements)


def test_optional_sync_demo_registers_read_only_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    from legacy.autogen_demo import base, sync_agent

    monkeypatch.setattr(
        sync_agent,
        "create_assistant_agent",
        lambda **kwargs: base.LocalAgent(name=kwargs["name"], system_message=kwargs["system_message"]),
    )
    executor = base.LocalAgent(name="ReadOnlyExecutor")

    agent = sync_agent.create_sync_agent(llm_config={"demo": True}, executor=executor)

    assert set(agent.registered_tools) == {
        "translate_text",
        "detect_and_translate",
        "get_product_list",
        "get_product_detail",
        "get_order_list",
    }


def test_optional_price_demo_has_no_product_write_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    from legacy.autogen_demo import base, price_agent

    monkeypatch.setattr(
        price_agent,
        "create_assistant_agent",
        lambda **kwargs: base.LocalAgent(name=kwargs["name"], system_message=kwargs["system_message"]),
    )
    executor = base.LocalAgent(name="ReadOnlyExecutor")

    agent = price_agent.create_price_agent(llm_config={"demo": True}, executor=executor)

    assert "update_product" not in agent.registered_tools
    assert set(agent.registered_tools) == {
        "fetch_competitor_prices",
        "generate_price_recommendation",
        "get_product_detail",
        "get_product_price",
    }


def test_runtime_httpx_supports_configured_socks_proxy() -> None:
    requirements = Path("requirements.runtime.txt").read_text(encoding="utf-8").splitlines()
    assert any(line.strip().lower() == "httpx[socks]==0.28.1" for line in requirements)


def test_production_import_graph_does_not_reference_legacy_autogen() -> None:
    production_roots = (Path("api"), Path("application"), Path("workflows"), Path("model_gateway"), Path("agents"))
    violations: list[str] = []
    for root in production_roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                module = ""
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                elif isinstance(node, ast.Import):
                    module = ",".join(alias.name for alias in node.names)
                if module.startswith("legacy.autogen_demo"):
                    violations.append(f"{path}:{getattr(node, 'lineno', 0)}")
    assert violations == []


def test_top_level_agents_package_does_not_export_group_chat() -> None:
    import agents

    assert not hasattr(agents, "create_omnisell_team")
    assert not hasattr(agents, "start_chat")
    for filename in ("base.py", "cs_agent.py", "price_agent.py", "sync_agent.py", "group_chat.py"):
        assert not (Path("agents") / filename).exists()

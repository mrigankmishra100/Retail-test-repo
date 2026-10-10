"""Offline smoke tests for the graph modules and FastAPI health route."""


def test_langgraph_modules_are_importable() -> None:
    from app.agents.retail_graph import build_retail_graph
    from app.agents.supplier_graph import build_supplier_graph

    assert callable(build_retail_graph)
    assert callable(build_supplier_graph)


def test_fastapi_health_route() -> None:
    import pytest

    pytest.importorskip("fastapi")
    pytest.importorskip("pydantic_settings")
    from app.main import app

    health_route = next(route for route in app.routes if getattr(route, "path", None) == "/health")
    assert health_route.endpoint() == {"status": "ok", "service": "retail-inventory-agent"}
    assert app.state.enterprise_ai_registry.agent_registry_enabled is False
    assert app.state.langsmith.enabled is False

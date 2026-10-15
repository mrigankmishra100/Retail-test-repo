"""Integration check: real MCP protocol and boundary with sample dependencies."""

import importlib.util
from pathlib import Path
import sys


def test_mcp_controls_demo():
    path = Path(__file__).resolve().parents[1] / "scripts" / "demo_mcp_controls.py"
    spec = importlib.util.spec_from_file_location("demo_mcp_controls", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        checks = module.run_demo()
    finally:
        sys.modules.pop(spec.name, None)
    assert len(checks) == 15
    assert all(check.expected == check.actual for check in checks)
    # All denied input/identity/approval/delegation requests stop before business
    # services. The invalid-result scenario intentionally executes a sample read.
    assert all(check.business_calls == 0 for check in checks
               if check.actual != "allowed" and check.name != "Invalid service output")

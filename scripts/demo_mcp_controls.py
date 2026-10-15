"""Exercise the real MCP HTTP boundary with isolated, in-memory sample services.

Run from the repository root with .venv/Scripts/python scripts/demo_mcp_controls.py.
No socket, database, model, OCI API, registry update or order is used. This is a
protocol/control demonstration, not proof of a deployed endpoint's configuration.
"""

from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
import argparse
import json
import logging
from secrets import token_urlsafe
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4


@dataclass(frozen=True)
class Check:
    name: str
    expected: str
    actual: str
    business_calls: int


class SampleServices:
    """Only deterministic fixtures; never construct the production container."""

    def __init__(self):
        self.calls = Counter()
        self.invalid_policy_result = False

    def fetch_all(self, operation, parameters):
        if operation != "supplier_identity":
            raise AssertionError("Unexpected database operation")
        return [{"supplier_id": "SUP001"}] if parameters["supplier_id"] == "SUP001" else []

    def get_suppliers_for_item(self, item_id):
        self.calls["suppliers"] += 1
        return [{"supplier_id": "SUP001", "supplier_name": "Sample supplier",
                 "item_id": item_id, "base_price": "900.00", "lead_time_days": 3,
                 "minimum_order_quantity": 100, "available_quantity": 500}]

    def get_inventory(self, supplier_id, item_id, *, session):
        self.calls["stock"] += 1
        return 500

    def get_supplier_policy(self, supplier_id, *, trusted_supplier_id):
        self.calls["policy"] += 1
        if self.invalid_policy_result:
            return {"invalid": "sample invalid service output"}
        raise AssertionError("This scenario should have been stopped before reading a policy")

    def get_inventory_risk_report(self, days):
        self.calls["risk"] += 1
        raise AssertionError("This scenario should have been stopped before reading inventory")

    def approval_context(self, case_id, *, session, tool_name):
        self.calls["approval_lookup"] += 1
        # Sample persisted-state result, deliberately no approval. The real
        # CaseService retrieves this evidence from the workflow database.
        return {"human_approved": False, "approval_event_ids": []}

    def send_supplier_request(self, case_id, supplier_id, *, session):
        self.calls["send"] += 1
        raise AssertionError("No supplier request may be sent by this demo")

    def business_calls(self):
        return sum(value for name, value in self.calls.items() if name != "approval_lookup")


def run_demo():
    """Return verified checks; raise AssertionError on any unexpected behavior."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.enterprise_ai.mcp_safety import EnterpriseAIMCPSafetyClient
    from app.mcp import tools
    from app.mcp.remote import assertion
    from app.mcp.server import mcp_http_app
    from app.oci.cache import OCICacheClient
    from app.services.sessions import SessionService

    from app.observability import LangfuseObservabilityClient
    sample = SampleServices()
    sessions = SessionService(OCICacheClient(allow_memory=True), sample, development=True)
    retail = sessions.create("retail-manager")
    supplier = sessions.create("supplier", "SUP001")
    signing_key = token_urlsafe(32)
    container = SimpleNamespace(
        langfuse=LangfuseObservabilityClient(),
        session_service=sessions, supplier_service=sample, policy_service=sample,
        inventory_service=sample, case_service=sample, replenishment_service=sample,
        mcp_safety=EnterpriseAIMCPSafetyClient(enabled=False),
        settings=SimpleNamespace(remote_mcp_service_keys={"retail": signing_key}),
    )

    @asynccontextmanager
    async def lifespan(application):
        async with mcp_http_app.lifespan(application):
            yield

    application = FastAPI(lifespan=lifespan)
    application.mount("/mcp", mcp_http_app)
    checks = []
    with patch.object(tools, "get_container", return_value=container), TestClient(application) as client:
        headers = {"Accept": "application/json, text/event-stream"}
        request_id = 0

        def rpc(method, parameters, extra_headers=None, *, notification=False):
            nonlocal request_id
            request_id += 1
            body = {"jsonrpc": "2.0", "method": method, "params": parameters}
            if not notification:
                body["id"] = request_id
            response = client.post("/mcp/", json=body, headers={**headers, **(extra_headers or {})})
            assert response.is_success, f"Unexpected HTTP status: {response.status_code}"
            return None if notification else response.json()

        initialized = rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                          "clientInfo": {"name": "isolated-controls-demo", "version": "1.0"}})
        headers["MCP-Protocol-Version"] = initialized["result"]["protocolVersion"]
        rpc("notifications/initialized", {}, notification=True)
        catalog = rpc("tools/list", {})["result"]["tools"]
        assert {tool["name"] for tool in catalog} == set(tools.TOOL_FUNCTIONS)
        # Discovery is public in this implementation; authorization is applied
        # to execution. Do not present a public catalog as an authentication bug.
        assert all("inputSchema" in tool for tool in catalog)
        checks.append(Check(f"Public schema discovery ({len(catalog)} tools)", "allowed", "allowed", 0))

        def call(label, name, arguments, expected, *, identity=retail, signed=None,
                 expected_calls=0, expected_data=None):
            before = sample.business_calls()
            auth = {"Authorization": "Bearer " + identity["session_token"]} if identity else {}
            if signed:
                auth["X-MCP-Assertion"] = signed
            reply = rpc("tools/call", {"name": name, "arguments": arguments}, auth)
            result = reply.get("result", {})
            error = result.get("isError", False) or "error" in reply
            message = " ".join(part.get("text", "") for part in result.get("content", []))
            if "error" in reply:
                message += reply["error"]["message"]
            actual = message.split(";", 1)[0] if error else "allowed"
            delta = sample.business_calls() - before
            assert actual == expected, f"{label}: expected {expected}, got {actual}"
            assert delta == expected_calls, f"{label}: unexpected business-service execution"
            if expected_data:
                assert expected_data(result.get("structuredContent")), f"{label}: incorrect result"
            if error:
                assert "trace_id=" in message, f"{label}: missing correlation reference"
                assert "sample invalid service output" not in message, f"{label}: raw output leaked"
            checks.append(Check(label, expected, actual, delta))

        call("Retail reads eligible suppliers", "get_suppliers_for_item", {"item_id": "ITEM001"},
             "allowed", expected_calls=1,
             expected_data=lambda value: value["suppliers"][0]["supplier_id"] == "SUP001")
        call("Supplier reads its own stock", "get_supplier_inventory",
             {"supplier_id": "SUP001", "item_id": "ITEM001"}, "allowed", identity=supplier,
             expected_calls=1, expected_data=lambda value: value["available_quantity"] == 500)
        call("Missing session", "get_suppliers_for_item", {"item_id": "ITEM001"},
             "unauthorized", identity=None)
        call("Invalid supplier ID", "get_supplier_policy", {"supplier_id": "SUP01"}, "validation_error")
        case_id = str(uuid4())
        call("Caller forges human approval", "send_supplier_request",
             {"case_id": case_id, "supplier_id": "SUP001", "human_approved": True}, "validation_error")
        call("Cross-supplier policy access", "get_supplier_policy", {"supplier_id": "SUP002"},
             "forbidden", identity=supplier)
        call("Supplier calls a retail-only tool", "get_inventory_risk", {"days": 7},
             "forbidden", identity=supplier)
        call("Send without recorded approval", "send_supplier_request",
             {"case_id": case_id, "supplier_id": "SUP001"}, "forbidden")
        assert sample.calls["approval_lookup"] == 1
        call("Unknown tool", "delete_all_inventory", {}, "unknown_tool")
        sample.invalid_policy_result = True
        call("Invalid service output", "get_supplier_policy", {"supplier_id": "SUP001"},
             "dependency_unavailable", expected_calls=1)
        sample.invalid_policy_result = False

        arguments = {"item_id": "ITEM001"}
        signed = assertion("get_suppliers_for_item", arguments,
                           sessions.resolve(retail["session_token"]), signing_key)
        call("Valid signed delegation", "get_suppliers_for_item", arguments, "allowed",
             identity=None, signed=signed, expected_calls=1)
        call("Replay of signed delegation", "get_suppliers_for_item", arguments, "forbidden",
             identity=None, signed=signed)
        fresh = assertion("get_suppliers_for_item", arguments,
                          sessions.resolve(retail["session_token"]), signing_key)
        call("Signed request body changed", "get_suppliers_for_item", {"item_id": "ITEM002"},
             "forbidden", identity=None, signed=fresh)
        sessions.revoke(retail["session_token"])
        call("Revoked bearer session", "get_suppliers_for_item", arguments, "unauthorized")
        assert sample.calls["send"] == 0
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print machine-readable results")
    parser.add_argument("--verbose", action="store_true", help="Include normal MCP boundary logs")
    options = parser.parse_args()
    previous = logging.root.manager.disable
    if not options.verbose:
        logging.disable(logging.CRITICAL)
    try:
        checks = run_demo()
    finally:
        logging.disable(previous)
    if options.json:
        print(json.dumps({"mode": "isolated MCP HTTP protocol demo; sample business services",
                          "approval_evidence": "sample fixture, not a live workflow database lookup",
                          "checks": [asdict(check) for check in checks], "supplier_requests_sent": 0}, indent=2))
    else:
        print("Real MCP HTTP boundary | Sample services | No live OCI/LLM/database calls")
        print("Approval evidence is a sample fixture, not a live workflow database lookup.")
        print("A rejected call is a PASS when the control correctly stops it.\n")
        for check in checks:
            print(f"PASS  {check.name:<43} {check.actual:<23} business calls: {check.business_calls}")
        print(f"\n{len(checks)} checks passed. Supplier requests sent: 0.")
        print("Hosted Oracle MCP Safety, prompt-injection scoring and deployed transport are not tested.")


if __name__ == "__main__":
    main()

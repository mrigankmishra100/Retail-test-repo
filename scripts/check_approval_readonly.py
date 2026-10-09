"""Inspect one saved draft against current facts. Never approve, dispatch or write."""
import json
import argparse
from pathlib import Path
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.dependencies import get_container
from app.oci.database import materialize_row
from app.services.cases import CaseConflict, digest

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("case_id", type=UUID)
args = parser.parse_args()
container = get_container()
try:
    connection = container.database._get_pool().acquire()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(
                "SELECT case_id, item_id, recommended_quantity, selected_supplier_id, "
                "status, version, draft_hash, draft_payload FROM replenishment_cases WHERE case_id=:case_id",
                case_id=str(args.case_id),
            )
            row = cursor.fetchone()
            if not row:
                raise SystemExit("Case not found")
            case = materialize_row([column[0].lower() for column in cursor.description], row)
    finally:
        connection.rollback()
        connection.close()
    payload = json.loads(case["draft_payload"])
    fresh = container.supplier_service.quote(case["selected_supplier_id"], case["item_id"], int(case["recommended_quantity"]))
    saved = payload["quote"]
    ignored = {"available_quantity", "history_quality", "policy_source"}
    changed = sorted(key for key in set(fresh) | set(saved) if key not in ignored and fresh.get(key) != saved.get(key))
    try:
        destination = bool(container.case_service._destination(payload, True))
        destination_issue = None if destination else "notification_destination_not_configured"
    except CaseConflict:
        destination = False
        destination_issue = "shared_poc_email_mode_disabled"
    print(json.dumps({
        "read_only": True, "status": case["status"], "version": case["version"],
        "draft_integrity_matches": digest(payload) == case["draft_hash"],
        "current_quote_feasible": fresh["feasible"],
        "current_infeasibility_reasons": fresh["infeasibility_reasons"],
        "changed_quote_fields": changed,
        "email_delivery_mode": (payload.get("supplier_email") or {}).get("delivery_mode"),
        "notification_destination_configured": destination, "destination_issue": destination_issue,
        "publishing_enabled": container.notifications.enabled,
    }))
finally:
    container.close()

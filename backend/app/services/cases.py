"""Transactional case workflow. Only dedicated human-decision APIs create approvals."""

import asyncio
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from uuid import UUID, uuid4, uuid5

from app.services.case_state import validate_transition
from app.services.sessions import SessionContext, SessionError
from app.oci.notifications import NotificationDeliveryError


class CaseConflict(ValueError):
    """Stale version, conflicting retry, or unsafe workflow action."""


class CaseNotFound(LookupError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False,
                      default=lambda v: v.isoformat() if isinstance(v, (date, datetime)) else str(v))


def digest(value):
    return sha256(canonical(value).encode()).hexdigest()


class CaseService:
    def __init__(self, database, suppliers, notifications, safety, email_drafts=None):
        self.database, self.suppliers = database, suppliers
        self.notifications, self.safety = notifications, safety
        self.email_drafts = email_drafts

    @staticmethod
    def _session(session):
        if not isinstance(session, SessionContext) or session.expires_at <= datetime.now(timezone.utc):
            raise SessionError("Trusted unexpired session required")
        return session

    def _case(self, tx, case_id, session):
        self._session(session)
        try:
            case_id = str(UUID(case_id))
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid case ID") from exc
        rows = tx.run("case", case_id=case_id)
        if not rows:
            raise CaseNotFound("Case not found")
        case = rows[0]
        if session.role == "supplier":
            # No pre-manager-approval draft exposure, even to the chosen supplier.
            if (case["selected_supplier_id"] != session.supplier_id or case["status"] in
                    {"DRAFT", "AWAITING_MANAGER_APPROVAL", "MANAGER_REJECTED", "REQUEST_QUEUED"}):
                raise CaseNotFound("Case not found")
        if not tx.run("origin", case_id=case_id):
            raise CaseConflict("Legacy case has no trustworthy creation history; explicit review is required")
        return case

    @staticmethod
    def _view(case):
        return {key: case.get(key) for key in ("case_id", "item_id", "current_stock", "recommended_quantity",
                                               "selected_supplier_id", "status", "version", "draft_hash")} | {
            "draft": json.loads(case["draft_payload"]) if case.get("draft_payload") else None}

    @staticmethod
    def _request(session, action, body, key):
        if not isinstance(key, str) or not 1 <= len(key) <= 128 or not key.isascii() or any(not 33 <= ord(c) <= 126 for c in key):
            raise ValueError("Idempotency-Key must contain 1-128 printable ASCII characters without spaces")
        return digest({"session_id": str(session.session_id), "role": session.role,
                       "supplier_id": session.supplier_id, "action": action, "body": body})

    @staticmethod
    def _replay(tx, case_id, key, request_hash):
        events = tx.run("event", case_id=case_id, key=key)
        if events:
            if events[0]["request_hash"] != request_hash:
                raise CaseConflict("Idempotency key already used for a different request")
            return json.loads(events[0]["result_snapshot"])

    def _event(self, tx, case, origin, event_type, payload, key, request_hash, session=None,
               decision=None, comment=None):
        event_id = str(uuid4())
        result = self._view(case)
        tx.run("insert_event", event_id=event_id, case_id=case["case_id"], version=case["version"],
               event_type=event_type, from_status=origin, to_status=case["status"],
               role=session.role if session else "system", session_id=str(session.session_id) if session else None,
               supplier_id=session.supplier_id if session else None, decision=decision, decision_comment=comment,
               key=key, request_hash=request_hash, payload=canonical(payload), hash=digest(payload),
               result=canonical(result))
        return event_id, result

    @staticmethod
    def _move(tx, case, target, payload=None, supplier_id=None):
        validate_transition(case["status"], target)
        old = case["version"]
        if payload is not None:
            case["draft_payload"], case["draft_hash"] = canonical(payload), digest(payload)
        if supplier_id is not None:
            case["selected_supplier_id"] = supplier_id
        case["version"], case["status"] = old + 1, target
        changed = tx.run("update_case", case_id=case["case_id"], old_version=old, version=case["version"],
                         status=target, supplier_id=case["selected_supplier_id"],
                         payload=case.get("draft_payload"), hash=case.get("draft_hash"))
        if changed != 1:
            raise CaseConflict("Case changed concurrently")

    def get_case(self, case_id, *, session):
        with self.database.case_transaction() as tx:
            case = self._case(tx, case_id, session)
            result = self._view(case)
            result["summary"] = next((row["summary"] for row in tx.run("memory", case_id=case_id)), None)
            # Expose delivery state, not destinations, provider metadata, or raw errors.
            result["notifications"] = [
                {"type": row["notification_type"], "status": row["status"]}
                for kind in ("SUPPLIER_REQUEST", "SUPPLIER_RESPONSE")
                for row in tx.run("outbox", case_id=case_id, kind=kind)]
            result["decisions"] = [{"role": row["actor_role"], "decision": row["decision"],
                                    "comment": row.get("decision_comment")}
                                   for row in tx.run("decisions", case_id=case_id)]
            return result

    def list_cases(self, *, session, limit=50, offset=0):
        self._session(session)
        if not 1 <= limit <= 100 or not 0 <= offset <= 10000:
            raise ValueError("Invalid pagination")
        rows = self.database.fetch_all("cases_page", {"supplier_id": session.supplier_id,
                                                     "page_size": limit + 1, "offset": offset})
        return dict(items=rows[:limit], limit=limit, offset=offset, has_more=len(rows)>limit)

    def draft_supplier_email(self, case_id, *, session):
        """Read the exact reviewable snapshot. No new wording after approval, no send."""
        self._session(session).require_role("retail-manager")
        case = self.get_case(case_id, session=session)
        email = (case.get("draft") or {}).get("supplier_email")
        if not email:
            raise CaseConflict("Prepare a new case with an email snapshot before reviewing or approving it")
        if digest(case["draft"]) != case["draft_hash"]:
            raise CaseConflict("Draft integrity check failed")
        return {"case_id": case_id, "version": case["version"], "draft_hash": case["draft_hash"], "email": email}

    def create_case(self, item_id, quantity, *, session, key):
        self._session(session).require_role("retail-manager")
        if type(quantity) is not int or not 1 <= quantity <= 1_000_000:
            raise ValueError("Quantity must be a whole number from 1 to 1000000")
        request_hash = self._request(session, "create", [item_id, quantity], key)
        # Server-derived UUID gives durable retry identity before the client knows a case ID.
        case_id = str(uuid5(session.session_id, "retail-case:" + key))
        with self.database.case_transaction() as tx:
            replay = self._replay(tx, case_id, key, request_hash)
            if replay is not None:
                return replay
        # Do not acquire a second pool connection while holding a transaction.
        facts = self.database.fetch_all("item_details", {"item_id": item_id})
        if not facts:
            raise ValueError("Unknown inventory item")
        stock = int(facts[0]["current_quantity"])
        for attempt in range(2):
            try:
                with self.database.case_transaction() as tx:
                    rows = tx.run("case", case_id=case_id)
                    if rows:
                        result = self._replay(tx, case_id, key, request_hash)
                        if result is None:
                            raise CaseConflict("Existing case has no trustworthy creation history")
                        return result
                    tx.run("insert_case", case_id=case_id, item_id=item_id, current_stock=stock, quantity=quantity)
                    case = dict(case_id=case_id, item_id=item_id, current_stock=stock, recommended_quantity=quantity,
                                status="DRAFT", version=0, selected_supplier_id=None, draft_payload=None, draft_hash=None)
                    return self._event(tx, case, None, "CREATED", {"item_id": item_id, "quantity": quantity},
                                       key, request_hash, session)[1]
            except Exception as exc:
                # Concurrent identical creation: Oracle unique violation, rollback, then replay.
                if attempt or getattr(exc.args[0] if exc.args else None, "code", None) != 1:
                    raise
        raise CaseConflict("Concurrent creation could not be replayed")

    def prepare(self, case_id, supplier_id, *, session, version, key):
        self._session(session).require_role("retail-manager")
        request_hash = self._request(session, "prepare", [supplier_id, version], key)
        with self.database.case_transaction() as tx:
            preview = self._case(tx, case_id, session)
            replay = self._replay(tx, case_id, key, request_hash)
            if replay is not None:
                return replay
            if preview["version"] != version or preview["status"] != "DRAFT":
                raise CaseConflict("Expected the current draft version")
        quote = self.suppliers.quote(supplier_id, preview["item_id"], preview["recommended_quantity"])
        if not quote["feasible"]:
            raise CaseConflict("Supplier/quantity/delivery is not feasible: " + ", ".join(quote["infeasibility_reasons"]))
        payload = {"item_id": preview["item_id"], "quantity": preview["recommended_quantity"],
                   "supplier_id": supplier_id, "quote": quote, "scope": "POC policy-priced goods only; see exclusions"}
        from app.services.notification_email import default_email_narratives
        narratives = (self.email_drafts.generate_narratives(payload) if self.email_drafts is not None
                      else default_email_narratives(quote["supplier_name"]))
        if self.email_drafts is not None:
            payload["supplier_email"] = self.email_drafts.draft(
                case_id, payload, narrative=narratives.request_summary)
        from app.services.supplier_response import draft_supplier_response
        payload["supplier_response"] = draft_supplier_response(case_id, payload,
            narrative=narratives.response_summary,
            shared_poc_enabled=getattr(self.notifications, "shared_poc_enabled", False) is True)
        with self.database.case_transaction() as tx:
            case = self._case(tx, case_id, session)
            replay = self._replay(tx, case_id, key, request_hash)
            if replay is not None:
                return replay
            if case["version"] != version or case["status"] != "DRAFT":
                raise CaseConflict("Expected the current draft version")
            self._move(tx, case, "AWAITING_MANAGER_APPROVAL", payload, supplier_id)
            return self._event(tx, case, "DRAFT", "STATE_CHANGED", payload, key, request_hash, session)[1]

    def decide(self, case_id, *, session, role, approved, version, draft_hash, key, comment=None):
        self._session(session).require_role(role)
        if type(approved) is not bool or (comment is not None and (not isinstance(comment, str) or len(comment.encode("utf-8")) > 2000)):
            raise ValueError("Invalid decision or comment")
        request_hash = self._request(session, role + "-decision", [approved, version, draft_hash, comment], key)
        manager = role == "retail-manager"
        expected = "AWAITING_MANAGER_APPROVAL" if manager else "AWAITING_SUPPLIER_APPROVAL"
        target = ("REQUEST_QUEUED" if manager else "RESPONSE_QUEUED") if approved else (
            "MANAGER_REJECTED" if manager else "SUPPLIER_REJECTED")
        with self.database.case_transaction() as tx:
            preview = self._case(tx, case_id, session)
            replay = self._replay(tx, case_id, key, request_hash)
            if replay is not None:
                return replay
            if preview["status"] != expected or preview["version"] != version or preview["draft_hash"] != draft_hash:
                raise CaseConflict("Decision does not match the current pending draft/version")
        fresh = (self.suppliers.quote(preview["selected_supplier_id"], preview["item_id"], preview["recommended_quantity"])
                 if approved else None)
        with self.database.case_transaction() as tx:
            case = self._case(tx, case_id, session)
            replay = self._replay(tx, case_id, key, request_hash)
            if replay is not None:
                return replay
            if case["status"] != expected or case["version"] != version or case["draft_hash"] != draft_hash:
                raise CaseConflict("Decision does not match the current pending draft/version")
            payload = json.loads(case["draft_payload"])
            if digest(payload) != draft_hash:
                raise CaseConflict("Draft integrity check failed")
            if approved:
                # Availability/history may change without altering commercial terms; feasibility is always rechecked.
                ignored = {"available_quantity", "history_quality", "policy_source"}
                if (not fresh["feasible"] or {k: v for k, v in fresh.items() if k not in ignored} !=
                        {k: v for k, v in payload["quote"].items() if k not in ignored}):
                    raise CaseConflict("Stock, policy or quote changed; create and review a fresh draft")
                destination = self._destination(payload, manager)
                if not destination:
                    raise CaseConflict("Notification destination is not configured")
            self._move(tx, case, target)
            event_type = "MANAGER_DECISION" if manager else "SUPPLIER_DECISION"
            event_id, result = self._event(tx, case, expected, event_type, payload, key, request_hash, session,
                                          "APPROVE" if approved else "REJECT", comment)
            if approved:
                kind = "SUPPLIER_REQUEST" if manager else "SUPPLIER_RESPONSE"
                tx.run("insert_outbox", notification_id=str(uuid4()), case_id=case_id, event_id=event_id,
                       event_type=event_type, kind=kind, destination=destination,
                       payload=canonical(self._message(case_id, kind, event_id, draft_hash,
                           payload.get("supplier_email") if manager else None, payload["supplier_id"],
                           payload.get("supplier_response") if not manager else None)))
            return result

    @staticmethod
    def _message(case_id, kind, event_id, payload_hash, email=None, supplier_id=None, response=None):
        # Email delivery mode is frozen in the approved snapshot; legacy messages stay wake-ups.
        if email is not None:
            return {"case_id": case_id, "kind": kind, "approval_event_id": event_id,
                    "payload_hash": payload_hash, "supplier_id": supplier_id, "email": email}
        if response and response.get("delivery_mode") == "shared_poc_topic":
            return {"case_id": case_id, "kind": kind, "approval_event_id": event_id,
                    "payload_hash": payload_hash, "supplier_id": supplier_id, "response_email": response}
        return {"case_id": case_id, "kind": kind, "approval_event_id": event_id, "payload_hash": payload_hash,
                "message": "An approved POC case update is available in the authenticated application."}

    def _destination(self, payload, manager):
        if not manager and (payload.get("supplier_response") or {}).get("delivery_mode") == "shared_poc_topic":
            if not getattr(self.notifications, "shared_poc_enabled", False):
                raise CaseConflict("Shared POC email mode is disabled")
            return self.notifications.topic_id
        if manager and payload.get("supplier_email"):
            if payload["supplier_email"].get("delivery_mode", "supplier_topic") == "shared_poc_topic":
                if not getattr(self.notifications, "shared_poc_enabled", False):
                    raise CaseConflict("Shared POC email mode is disabled")
                return self.notifications.topic_id
            return self.notifications.supplier_topics.get(payload["supplier_id"])
        return self.notifications.topic_id

    def _verify_approval(self, tx, case, outbox, *, check_destination=True):
        events = tx.run("approvals", case_id=case["case_id"])
        event = next((e for e in events if e["event_id"] == outbox["approval_event_id"]), None)
        kind = outbox["notification_type"]
        manager = kind == "SUPPLIER_REQUEST"
        role, event_type = ("retail-manager", "MANAGER_DECISION") if manager else ("supplier", "SUPPLIER_DECISION")
        if (not event or event["event_type"] != event_type or event["actor_role"] != role
                or not event["actor_session_id"] or event["decision"] != "APPROVE"
                or (not manager and event["actor_supplier_id"] != case["selected_supplier_id"])):
            raise CaseConflict("Matching trusted approval is missing")
        payload = json.loads(event["payload_snapshot"])
        if (digest(payload) != event["payload_hash"] or event["payload_hash"] != case["draft_hash"]
                or json.loads(case["draft_payload"]) != payload
                or payload["supplier_id"] != case["selected_supplier_id"]
                or payload["item_id"] != case["item_id"] or payload["quantity"] != case["recommended_quantity"]
                or json.loads(outbox["message_payload"]) != self._message(case["case_id"], kind, event["event_id"], event["payload_hash"],
                    payload.get("supplier_email") if manager else None, payload["supplier_id"],
                    payload.get("supplier_response") if not manager else None)
                or (check_destination and outbox["destination"] != self._destination(payload, manager))):
            raise CaseConflict("Approved payload or destination integrity check failed")
        return event

    def approved_memory(self, case_id, *, session):
        """Historical evidence remains valid across topic rotations; never invent long-term facts."""
        with self.database.case_transaction() as tx:
            case = self._case(tx, case_id, session)
            if case["status"] != "COMPLETED":
                raise CaseNotFound("Approved completed memory not found")
            completed = tx.run("completion", case_id=case_id, version=case["version"])
            if len(completed) != 1 or completed[0]["payload_hash"] != case["draft_hash"]:
                raise CaseConflict("Completion evidence is missing or inconsistent")
            for kind in ("SUPPLIER_REQUEST", "SUPPLIER_RESPONSE"):
                rows = tx.run("outbox", case_id=case_id, kind=kind)
                if not rows or rows[0]["status"] != "SENT":
                    raise CaseConflict("Approved delivery evidence is missing")
                self._verify_approval(tx, case, rows[0], check_destination=False)
            memory = tx.run("memory", case_id=case_id)
            expected = {"case_id": case_id, "status": "COMPLETED", "approved_terms": json.loads(case["draft_payload"])}
            if len(memory) != 1 or json.loads(memory[0]["summary"]) != expected:
                raise CaseConflict("Approved memory integrity check failed")
            return expected

    def approval_context(self, case_id, *, session, tool_name):
        """MCP preflight evidence; each actual mutation independently re-verifies it."""
        kinds = {"send_supplier_request": ("SUPPLIER_REQUEST",),
                 "send_supplier_response": ("SUPPLIER_RESPONSE",),
                 "close_replenishment_case": ("SUPPLIER_REQUEST", "SUPPLIER_RESPONSE")}
        if tool_name not in kinds:
            raise PermissionError("Unknown controlled tool")
        self._session(session)
        if tool_name != "close_replenishment_case":
            session.require_role("supplier" if tool_name == "send_supplier_response" else "retail-manager")
        with self.database.case_transaction() as tx:
            case = self._case(tx, case_id, session)
            events = []
            for kind in kinds[tool_name]:
                rows = tx.run("outbox", case_id=case_id, kind=kind)
                if not rows:
                    raise CaseConflict("Matching approved intent is missing")
                if tool_name == "close_replenishment_case" and rows[0]["status"] != "SENT":
                    raise CaseConflict("Both approved deliveries must be acknowledged")
                events.append(self._verify_approval(tx, case, rows[0])["event_id"])
            return self._safety_context(session, case_id, events)

    @staticmethod
    def _safety_context(session, case_id, events):
        return {"human_approved": True, "case_id": case_id, "approval_event_ids": events,
                "session_id": str(session.session_id), "role": session.role, "supplier_id": session.supplier_id}

    def dispatch(self, case_id, *, session, kind):
        self._session(session).require_role("retail-manager" if kind == "SUPPLIER_REQUEST" else "supplier")
        if kind not in {"SUPPLIER_REQUEST", "SUPPLIER_RESPONSE"}:
            raise ValueError("Invalid notification kind")
        expected = "REQUEST_QUEUED" if kind == "SUPPLIER_REQUEST" else "RESPONSE_QUEUED"
        token = str(uuid4())
        with self.database.case_transaction() as tx:
            case = self._case(tx, case_id, session)
            rows = tx.run("outbox", case_id=case_id, kind=kind)
            if not rows:
                raise CaseConflict("No approved notification intent")
            outbox = rows[0]
            approval = self._verify_approval(tx, case, outbox)
            if outbox["status"] == "SENT":
                return self._view(case)
            if case["status"] != expected:
                raise CaseConflict("Case is not awaiting this delivery")
            if outbox["status"] == "SENDING":
                expired = tx.run("expire_claim", id=outbox["notification_id"])
                # Return rather than raise so the UNKNOWN mark is committed.
                return {"case_id": case_id, "delivery_status": "UNKNOWN" if expired else "SENDING"}
            if outbox["status"] != "PENDING":
                raise CaseConflict("Delivery requires operator reconciliation; it will not be resent")
            self.notifications.ensure_ready()
            tool = "send_supplier_request" if kind == "SUPPLIER_REQUEST" else "send_supplier_response"
            safety = asyncio.run(self.safety.validate_tool_call(tool, self._safety_context(session, case_id, [approval["event_id"]])))
            if not safety["allowed"]:
                raise PermissionError("Controlled action denied by MCP Safety")
            if tx.run("claim", id=outbox["notification_id"], token=token) != 1:
                return {"case_id": case_id, "delivery_status": "PENDING"}
        # No external call before the durable claim commits.
        try:
            provider_id = self.notifications.publish(outbox["destination"], outbox["message_payload"], outbox["notification_id"])
        except Exception as exc:
            known = isinstance(exc, NotificationDeliveryError) and exc.not_delivered
            status = "PENDING" if known and exc.retryable and outbox["attempt_count"] < 2 else ("FAILED" if known else "UNKNOWN")
            with self.database.case_transaction() as tx:
                self._case(tx, case_id, session)
                tx.run("delivery_error", id=outbox["notification_id"], token=token, status=status,
                       code=exc.code if isinstance(exc, NotificationDeliveryError) else "delivery_uncertain")
            return {"case_id": case_id, "delivery_status": status}
        with self.database.case_transaction() as tx:
            # Resolve case again; a lost/expired claim must never acknowledge another worker's attempt.
            case = self._case(tx, case_id, session)
            if case["status"] != expected or tx.run("finish_delivery", id=outbox["notification_id"], token=token,
                                                    provider_id=provider_id) != 1:
                raise CaseConflict("Delivery acknowledgement needs reconciliation")
            target = "REQUEST_SENT" if kind == "SUPPLIER_REQUEST" else "SUPPLIER_RESPONDED"
            payload = json.loads(case["draft_payload"])
            self._move(tx, case, target)
            self._event(tx, case, expected, "STATE_CHANGED", payload, "delivery:" + outbox["notification_id"],
                        digest({"notification_id": outbox["notification_id"]}))
            if kind == "SUPPLIER_REQUEST":
                self._move(tx, case, "AWAITING_SUPPLIER_APPROVAL")
                self._event(tx, case, "REQUEST_SENT", "STATE_CHANGED", payload, "supplier-review:" + outbox["notification_id"],
                            digest({"notification_id": outbox["notification_id"], "action": "review"}))
            return self._view(case)

    def close_case(self, case_id, *, session, key):
        # _case enforces supplier isolation; both approved deliveries remain mandatory.
        self._session(session)
        request_hash = self._request(session, "complete", [], key)
        with self.database.case_transaction() as tx:
            case = self._case(tx, case_id, session)
            replay = self._replay(tx, case_id, key, request_hash)
            if replay is not None:
                return replay
            if case["status"] == "COMPLETED":
                return self._view(case)
            if case["status"] != "SUPPLIER_RESPONDED":
                raise CaseConflict("Case requires an approved, delivered supplier response")
            approval_ids = []
            for kind in ("SUPPLIER_REQUEST", "SUPPLIER_RESPONSE"):
                rows = tx.run("outbox", case_id=case_id, kind=kind)
                if not rows or rows[0]["status"] != "SENT":
                    raise CaseConflict("Both approved deliveries must be acknowledged")
                approval_ids.append(self._verify_approval(tx, case, rows[0])["event_id"])
            safety = asyncio.run(self.safety.validate_tool_call("close_replenishment_case", self._safety_context(session, case_id, approval_ids)))
            if not safety["allowed"]:
                raise PermissionError("Completion denied by MCP Safety")
            payload = json.loads(case["draft_payload"])
            self._move(tx, case, "COMPLETED")
            # Deterministic approved snapshot, never caller/LLM-authored memory.
            summary = canonical({"case_id": case_id, "status": "COMPLETED", "approved_terms": payload})
            tx.run("insert_memory", memory_id=str(uuid4()), case_id=case_id, summary=summary)
            return self._event(tx, case, "SUPPLIER_RESPONDED", "COMPLETED", payload, key, request_hash, session)[1]

"""Fixed statements for the case unit of work. Never accept caller-authored SQL."""

# Values are bound; table names, predicates and ordering are fixed in this module.
SQL = {
    "decisions": "SELECT actor_role, decision, decision_comment, case_version FROM replenishment_case_events WHERE case_id=:case_id AND decision IS NOT NULL ORDER BY case_version",
    "origin": "SELECT event_id FROM replenishment_case_events WHERE case_id=:case_id AND case_version=0 AND event_type='CREATED'",
    "case": "SELECT * FROM replenishment_cases WHERE case_id=:case_id FOR UPDATE",
    "event": "SELECT * FROM replenishment_case_events WHERE case_id=:case_id AND idempotency_key=:key",
    "approvals": "SELECT * FROM replenishment_case_events WHERE case_id=:case_id AND decision='APPROVE' ORDER BY case_version",
    "memory": "SELECT summary FROM approved_memory WHERE case_id=:case_id",
    "completion": "SELECT payload_hash FROM replenishment_case_events WHERE case_id=:case_id AND event_type='COMPLETED' AND case_version=:version",
    "outbox": "SELECT * FROM notification_outbox WHERE case_id=:case_id AND notification_type=:kind FOR UPDATE",
    "insert_case": """INSERT INTO replenishment_cases
        (case_id,item_id,current_stock,recommended_quantity,status)
        VALUES (:case_id,:item_id,:current_stock,:quantity,'DRAFT')""",
    "update_case": """UPDATE replenishment_cases SET status=:status,version=:version,
        selected_supplier_id=:supplier_id,draft_payload=:payload,draft_hash=:hash,updated_at=SYSTIMESTAMP
        WHERE case_id=:case_id AND version=:old_version""",
    "insert_event": """INSERT INTO replenishment_case_events
        (event_id,case_id,case_version,event_type,from_status,to_status,actor_role,actor_session_id,
         actor_supplier_id,decision,decision_comment,idempotency_key,request_hash,payload_snapshot,payload_hash,result_snapshot)
        VALUES (:event_id,:case_id,:version,:event_type,:from_status,:to_status,:role,:session_id,
                :supplier_id,:decision,:decision_comment,:key,:request_hash,:payload,:hash,:result)""",
    "insert_outbox": """INSERT INTO notification_outbox
        (notification_id,case_id,approval_event_id,approval_event_type,notification_type,destination,message_payload)
        VALUES (:notification_id,:case_id,:event_id,:event_type,:kind,:destination,:payload)""",
    "insert_memory": "INSERT INTO approved_memory (memory_id,case_id,summary) VALUES (:memory_id,:case_id,:summary)",
    "claim": """UPDATE notification_outbox SET status='SENDING',claim_token=:token,
        lease_expires_at=SYSTIMESTAMP + INTERVAL '2' MINUTE,attempt_count=attempt_count+1,updated_at=SYSTIMESTAMP
        WHERE notification_id=:id AND status='PENDING' AND next_attempt_at<=SYSTIMESTAMP""",
    "expire_claim": """UPDATE notification_outbox SET status='UNKNOWN',claim_token=NULL,lease_expires_at=NULL,
        last_error_code='expired_claim',updated_at=SYSTIMESTAMP
        WHERE notification_id=:id AND status='SENDING' AND lease_expires_at<=SYSTIMESTAMP""",
    "finish_delivery": """UPDATE notification_outbox SET status='SENT',sent_at=SYSTIMESTAMP,
        provider_message_id=:provider_id,claim_token=NULL,lease_expires_at=NULL,updated_at=SYSTIMESTAMP
        WHERE notification_id=:id AND status='SENDING' AND claim_token=:token""",
    "delivery_error": """UPDATE notification_outbox SET status=:status,last_error_code=:code,
        next_attempt_at=SYSTIMESTAMP + INTERVAL '1' MINUTE,claim_token=NULL,lease_expires_at=NULL,updated_at=SYSTIMESTAMP
        WHERE notification_id=:id AND status='SENDING' AND claim_token=:token""",
}

# Mark long text binds explicitly; OCI/Oracle must not infer VARCHAR2 for large JSON.
CLOB_BINDS = {"payload", "result", "summary"}

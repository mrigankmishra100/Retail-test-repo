"""Supplier request snapshots, reviewed before human approval."""

from app.contracts import SupplierEmailDraft, QuoteResponse
from app.services.notification_email import EmailNarrativeGenerator, render_notification_email


class EmailDraftService:
    def __init__(self, database, *, responses=None, shared_poc_enabled=False):
        self.database = database
        self.shared_poc_enabled = shared_poc_enabled
        self.narratives = EmailNarrativeGenerator(responses)

    def generate_narratives(self, payload):
        quote = QuoteResponse.model_validate(payload["quote"])
        return self.narratives.generate(quote.supplier_name)

    def draft(self, case_id, payload, *, narrative=None):
        quote = QuoteResponse.model_validate(payload["quote"])
        rows = self.database.fetch_all("supplier_email", {"supplier_id": payload["supplier_id"]})
        if len(rows) != 1:
            raise ValueError("Supplier email is unavailable")
        narrative = narrative or self.narratives.generate(quote.supplier_name).request_summary
        rendered = render_notification_email(case_id, payload, kind="request", narrative=narrative,
                                             shared_poc_enabled=self.shared_poc_enabled)
        return SupplierEmailDraft(recipient=rows[0]["email"], subject=rendered.subject, body=rendered.body,
            template_version="supplier-request-v3",
            delivery_mode="shared_poc_topic" if self.shared_poc_enabled else "supplier_topic").model_dump(mode="json")

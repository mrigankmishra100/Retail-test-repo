"""Reviewable response derived only from the case's frozen commercial terms."""

from app.contracts import QuoteResponse, SupplierResponseDraft
from app.services.notification_email import default_email_narratives, render_notification_email


def draft_supplier_response(case_id, payload, *, narrative=None, shared_poc_enabled=False):
    quote = QuoteResponse.model_validate(payload["quote"])
    narrative = narrative or default_email_narratives(quote.supplier_name).response_summary
    rendered = render_notification_email(case_id, payload, kind="response", narrative=narrative,
                                         shared_poc_enabled=shared_poc_enabled)
    return SupplierResponseDraft(subject=rendered.subject, body=rendered.body,
        template_version="supplier-response-v4",
        delivery_mode="shared_poc_topic" if shared_poc_enabled else "case_update_topic").model_dump(mode="json")

"""Shared, reviewable plain-text notification content."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import json
import logging
import re
from time import monotonic
from textwrap import TextWrapper

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.contracts import QuoteResponse

logger = logging.getLogger(__name__)

LINE_WIDTH = 72


class EmailNarratives(BaseModel):
    """The only prose the model may contribute to the two frozen drafts."""

    model_config = ConfigDict(extra="forbid")
    request_summary: str = Field(min_length=20, max_length=280)
    response_summary: str = Field(min_length=20, max_length=280)

    @field_validator("request_summary", "response_summary")
    @classmethod
    def safe_plain_text(cls, value: str) -> str:
        value = " ".join(value.split())
        if any(character.isdigit() for character in value):
            raise ValueError("Narrative must not contain numbers")
        if re.search(r"[#*_`<>\[\]{}]|\b(?:INR|USD|price|quantity|total|payment|delivery|lead time|valid until)\b", value,
                     flags=re.IGNORECASE):
            raise ValueError("Narrative must not contain markup or commercial terms")
        if len(re.findall(r"[.!?](?:\s|$)", value)) > 2:
            raise ValueError("Narrative must contain at most two sentences")
        return value


def default_email_narratives(_supplier_name: str) -> EmailNarratives:
    return EmailNarratives(
        request_summary=(
            "The supplier is invited to review the manager-approved replenishment request. "
            "The verified order details and commercial terms are provided below."
        ),
        response_summary=(
            "Supplier approval has been recorded for this replenishment response. "
            "The retail team can review the approved terms below."
        ),
    )


class EmailNarrativeGenerator:
    """Generate both short summaries in one optional model call."""

    def __init__(self, responses=None):
        self.responses = responses

    def generate(self, supplier_name: str) -> EmailNarratives:
        started, outcome = monotonic(), 'deterministic_fallback'
        fallback = default_email_narratives(supplier_name)
        if self.responses is None:
            return fallback
        messages = [
            {
                "role": "system",
                "content": (
                    "Write concise plain-text notification summaries using only the supplied workflow facts. "
                    "Return one JSON object with exactly request_summary and response_summary. Each value must "
                    "contain one or two short sentences. Do not include Markdown, numbers, identifiers, prices, "
                    "quantities, dates, payment terms, delivery terms, promises, or facts not supplied. The request "
                    "summary may say manager approval was recorded and ask the supplier to review. The response "
                    "summary may say supplier approval was recorded and ask the retail team to review."
                ),
            },
            {
                "role": "user",
                "content": json.dumps({"supplier_name": supplier_name}, ensure_ascii=False),
            },
        ]
        try:
            result = self.responses.create_response_sync(messages)
            if result.get("status") != "completed" or not isinstance(result.get("output"), str):
                return fallback
            generated = EmailNarratives.model_validate(json.loads(result["output"]))
            outcome = 'responses_completed'
            logger.info("Accepted AI-assisted email narratives")
            return generated
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
            logger.warning("Rejected AI-assisted email narratives; using deterministic fallback (%s)",
                           type(exc).__name__)
            return fallback
        except Exception as exc:  # External model failures must not block a reviewable draft.
            logger.warning("Email narrative generation unavailable; using deterministic fallback (%s)",
                           type(exc).__name__)
            return fallback
        finally:
            logger.info('notification_narrative_completed', extra={'status': outcome,
                'duration_ms': round((monotonic()-started)*1000, 2)})


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    body: str


def _wrapped(text: str, *, initial: str = "", subsequent: str = "") -> list[str]:
    wrapper = TextWrapper(width=LINE_WIDTH, initial_indent=initial, subsequent_indent=subsequent,
                          break_long_words=True, break_on_hyphens=True)
    return wrapper.wrap(" ".join(str(text).split())) or [initial.rstrip()]


def _field(label: str, value) -> list[str]:
    prefix = f"{label}: "
    return _wrapped(str(value), initial=prefix, subsequent=" " * len(prefix))


def _bullet(value: str) -> list[str]:
    return _wrapped(value, initial="- ", subsequent="  ")


def _money(currency: str, value: Decimal | None) -> str:
    return "Not stated" if value is None else f"{currency} {Decimal(value):,.2f}"


def _date(value: date) -> str:
    return value.strftime("%d %b %Y")


def _unit(unit: str, quantity: int) -> str:
    return unit if quantity == 1 or unit.endswith("s") else f"{unit}s"


def render_notification_email(case_id: str, payload: dict, *, kind: str, narrative: str,
                              shared_poc_enabled: bool) -> RenderedEmail:
    """Render a compact body that remains readable in OCI's plain-text email."""
    if kind not in {"request", "response"}:
        raise ValueError("Unknown notification email kind")
    quote = QuoteResponse.model_validate(payload["quote"])
    quantity = payload["quantity"]
    profiles = {
        "request": {
            "headline": "REPLENISHMENT APPROVAL REQUEST",
            "subject": "Approval requested",
            "highlights": [
                "Manager approval has been recorded.",
                "Verified commercial terms are ready for supplier review.",
                "A supplier decision must be recorded in the application.",
            ],
            "next_step": "Approve or reject using the supplier application's decision controls.",
            "disclaimer": "This is a POC replenishment request, not a confirmed purchase order.",
        },
        "response": {
            "headline": "SUPPLIER RESPONSE CONFIRMED",
            "subject": "Supplier approved",
            "highlights": [
                "Supplier approval has been recorded.",
                "The approved commercial terms are preserved in the case.",
                "The retail team can continue from the Cases and Approvals workspace.",
            ],
            "next_step": "Review the confirmed response in the Cases and Approvals workspace.",
            "disclaimer": "This response is not a purchase order or proof of physical delivery.",
        },
    }
    profile = profiles[kind]
    lines: list[str] = []
    if shared_poc_enabled:
        lines.extend(["POC SHARED-TOPIC NOTICE",
                      "All confirmed subscribers can view this message.", ""])
    lines.append(profile["headline"])
    lines.append("")
    lines.extend(_wrapped(narrative))

    lines.extend(["", "KEY HIGHLIGHTS"])
    for highlight in profile["highlights"]:
        lines.extend(_bullet(highlight))

    lines.extend(["", "ORDER SUMMARY"])
    fields = [
        ("Supplier", f"{quote.supplier_name} ({quote.supplier_id})"),
        ("Item", payload["item_id"]),
        ("Quantity", f"{quantity} {_unit(quote.unit, quantity)}"),
        ("Unit price", _money(quote.currency, quote.unit_price)),
        ("Merchandise total", _money(quote.currency, quote.priced_total)),
        ("Lead time", "Not stated" if quote.lead_time_days is None else f"{quote.lead_time_days} days"),
        ("Valid until", _date(quote.valid_until)),
    ]
    for label, value in fields:
        lines.extend(_field(label, value))

    lines.extend(["", "COMMERCIAL TERMS"])
    lines.extend(_bullet(f"Payment: {quote.payment_terms}"))
    lines.extend(_bullet(f"Delivery: {quote.delivery_terms}"))

    lines.extend(["", "SPECIAL CONDITIONS"])
    lines.extend(_bullet(quote.special_conditions))

    lines.extend(["", "EXCLUSIONS"])
    for exclusion in quote.exclusions or ["None stated"]:
        lines.extend(_bullet(exclusion))

    lines.extend(["", "NEXT STEP"])
    lines.extend(_wrapped(profile["next_step"]))
    lines.extend(_wrapped("Replying to this notification does not record a decision."))

    lines.extend(["", "REFERENCE"])
    lines.extend(_field("Case ID", case_id))
    lines.extend([""])
    lines.extend(_wrapped(profile["disclaimer"]))
    lines.extend(_wrapped("No reply is required."))

    prefix = "[POC] " if shared_poc_enabled else ""
    subject = f"{prefix}{profile['subject']} | {payload['item_id']} | {case_id[:8]}"
    body = "\n".join(lines)
    if any(len(line) > LINE_WIDTH for line in body.splitlines()):
        raise ValueError("Notification email contains an overlong line")
    return RenderedEmail(subject=subject, body=body)

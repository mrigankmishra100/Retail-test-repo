"""Fail-closed parser for the supplied POC policy format, never free-form inference."""

from datetime import date, datetime
from decimal import Decimal
import re

from app.services.policy import PolicyDocumentError


DELIVERY = {
    "SUP001": "Standard delivery is based on the lead time listed above. Expedited delivery may be requested for Ice Cream and Milk at a 5% surcharge, subject to inventory and logistics confirmation.",
    "SUP002": "Standard delivery follows the lead times listed above. Ice Cream may be expedited to 3 days with a 7% surcharge, subject to capacity confirmation.",
    "SUP003": "Standard delivery follows the lead times listed above. Expedited service is not guaranteed and must be explicitly confirmed by supplier operations before commitment.",
    "SUP004": "Standard delivery follows the lead times listed above. A one-day acceleration may be requested for Soft Drinks or Detergent at a 4% surcharge, subject to supplier approval.",
}
SPECIAL = {
    "SUP001": "Cold-chain transportation is included for Ice Cream and Milk. Weekend delivery requires supplier confirmation. Split delivery may be proposed when full quantity is unavailable.",
    "SUP002": "Bulk pricing applies to a single approved replenishment case and cannot be aggregated across unrelated cases. Partial fulfillment may be offered if supplier inventory is below the requested quantity.",
    "SUP003": "Weekend delivery is subject to confirmation. Supplier may propose partial delivery for urgent retail cases. Product substitutions are not permitted without explicit retail manager approval.",
    "SUP004": "Cold-chain handling for Ice Cream is included in the listed price. Weekend delivery is subject to route availability. Supplier may recommend split delivery when it reduces stock-out risk.",
}
CONTROL = ("These terms are synthetic demonstration data for the Retail Inventory Replenishment POC. "
           "They do not constitute a legally binding contract, purchase order, or supplier commitment. "
           "Inventory availability, pricing, delivery dates and any expedited service must be confirmed "
           "in the active replenishment case. No commercial communication is sent until the authorized "
           "human approval step is completed.")


def parse_policy_terms(document: dict, *, as_of: date | None = None) -> dict:
    """Parse every commercial section; reject unknown clauses, gaps and duplicates."""
    text = " ".join(document["content"].replace("\ufffd", " ").replace("•", " ").split())
    supplier = document["supplier_id"]
    match = re.fullmatch(
        r"SUPPLIER COMMERCIAL & FULFILLMENT POLICY Retail Inventory Replenishment POC - One-Page Supplier Policy "
        r"Supplier (?P<name>[^()]+) \(" + re.escape(supplier) + r"\) Policy ID POC-" + re.escape(supplier) +
        r"-2026 Contact [^ ]+ Validity (?P<start>\d{2} [A-Za-z]{3} \d{4}) - (?P<end>\d{2} [A-Za-z]{3} \d{4}) "
        r"1\. Approved Products and Standard Terms Item ID Product MOQ Base Price Std\. Lead (?P<products>.+?) "
        r"2\. Volume Pricing (?P<volume>.+?) 3\. Delivery, Payment and Fulfillment Conditions "
        r"Delivery (?P<delivery>.+?) Payment (?P<payment>.+?) Special Conditions (?P<special>.+?) "
        r"POC CONTROL: (?P<control>.+?) Object Storage filename: " + re.escape(supplier) +
        r"_policy\.pdf \| Direct document read - no RAG/vector retrieval", text)
    if not match:
        raise PolicyDocumentError("unsupported_policy_format")
    fields = match.groupdict()
    if (fields["delivery"] != DELIVERY.get(supplier) or fields["special"] != SPECIAL.get(supplier)
            or fields["payment"] != "Net 30 days from invoice date." or fields["control"] != CONTROL):
        raise PolicyDocumentError("unsupported_policy_clause")
    try:
        start, end = (datetime.strptime(fields[key], "%d %b %Y").date() for key in ("start", "end"))
    except ValueError as exc:
        raise PolicyDocumentError("invalid_policy_validity") from exc
    if not start <= (as_of or date.today()) <= end:
        raise PolicyDocumentError("policy_outside_validity")
    products = {}
    row_pattern = r"(ITEM\d{3}) ([A-Za-z ]+?) (\d+) ([a-z]+) INR (\d+(?:\.\d{1,2})?) / ([a-z]+) (\d+) days"
    remaining = fields["products"]
    while remaining:
        row = re.match(row_pattern, remaining)
        if not row:
            raise PolicyDocumentError("invalid_product_table")
        item, name, moq, units, price, unit, lead = row.groups()
        if item in products or units not in {unit + "s", unit + "es"} or min(int(moq), int(lead)) < 1:
            raise PolicyDocumentError("invalid_or_duplicate_product")
        products[item] = dict(item_name=name, moq=int(moq), base_price=Decimal(price),
                              unit=unit, lead_time_days=int(lead), tiers=[])
        remaining = remaining[row.end():].strip()
    remaining = fields["volume"]
    for product in products.values():
        prefix = product["item_name"] + ": "
        if not remaining.startswith(prefix):
            raise PolicyDocumentError("missing_or_reordered_volume_terms")
        ending = " per " + product["unit"] + "."
        section, separator, remaining = remaining[len(prefix):].partition(ending)
        if not separator:
            raise PolicyDocumentError("invalid_volume_terms")
        remaining = remaining.strip()
        expected_min, previous_price = product["moq"], product["base_price"]
        for index, tier_text in enumerate(section.split("; ")):
            tier = re.fullmatch(r"(\d+)(?:-(\d+)|(\+))(?: ([a-z]+))? at INR (\d+(?:\.\d{1,2})?)", tier_text)
            if not tier:
                raise PolicyDocumentError("invalid_volume_tier")
            minimum, maximum, plus, units, price = tier.groups()
            low, high, amount = int(minimum), int(maximum) if maximum else None, Decimal(price)
            if (expected_min is None or low != expected_min or (high is not None and high < low)
                    or amount <= 0 or amount > previous_price or (index == 0 and amount != product["base_price"])
                    or (units and units not in {product["unit"] + "s", product["unit"] + "es"})):
                raise PolicyDocumentError("inconsistent_volume_tiers")
            product["tiers"].append((low, high, amount))
            expected_min, previous_price = None if high is None else high + 1, amount
        if expected_min is not None:
            raise PolicyDocumentError("unbounded_final_tier_required")
    if remaining:
        raise PolicyDocumentError("unparsed_volume_terms")
    return dict(products=products, currency="INR", payment_terms=fields["payment"],
                delivery_terms=fields["delivery"], special_conditions=fields["special"],
                valid_until=end.isoformat(), policy_sha256=document["sha256"], source=document["source"])


def expedited_terms(supplier_id: str, item_id: str, standard_days: int) -> tuple[Decimal, int | None]:
    """Only called after exact delivery-clause validation above."""
    if supplier_id == "SUP001" and item_id in {"ITEM001", "ITEM002"}:
        return Decimal("0.05"), None  # The document does not promise a faster lead time.
    if supplier_id == "SUP002" and item_id == "ITEM001":
        return Decimal("0.07"), 3
    if supplier_id == "SUP004" and item_id in {"ITEM004", "ITEM005"}:
        return Decimal("0.04"), max(1, standard_days - 1)
    raise PolicyDocumentError("expedited_terms_not_priced")

"""Supplier relationships and supplier inventory service boundary."""

from typing import Any
from decimal import Decimal, ROUND_HALF_UP

from app.oci.database import OracleDatabaseClient
from app.services.sessions import SessionContext, SessionError
from app.services.policy import PolicyService, PolicyDocumentError
from app.services.policy_terms import parse_policy_terms, expedited_terms
from app.services.inventory import InventoryService, ItemNotFound


class SupplierService:
    """Coordinate supplier lookup, item eligibility, inventory, and quotes."""

    def __init__(self, database_client: OracleDatabaseClient | None = None, *,
                 policy_service: PolicyService | None = None, inventory_service: InventoryService | None = None) -> None:
        self.database_client = database_client or OracleDatabaseClient()
        self.policy_service = policy_service or PolicyService()
        self.inventory_service = inventory_service or InventoryService(self.database_client)

    def get_suppliers_for_item(self, item_id: str) -> list[dict[str, Any]]:
        """Return only item-eligible supplier relationships from Oracle."""
        return self.database_client.fetch_all("suppliers_for_item", {"item_id": item_id})

    def get_inventory(self, supplier_id: str, item_id: str, *, session: SessionContext | None = None) -> int | None:
        """Return authenticated supplier inventory for a future implementation."""
        if session is None:
            raise SessionError("Trusted supplier session required")
        session.require_supplier(supplier_id)
        rows = self.database_client.fetch_all("supplier_inventory", {"supplier_id": supplier_id, "item_id": item_id})
        return rows[0]["available_quantity"] if rows else None

    def calculate_quote(self, supplier_id: str, item_id: str, quantity: int, *,
                        session: SessionContext | None = None, expedited: bool = False) -> dict[str, Any]:
        """Supplier-scoped quote using the same calculator as manager comparisons."""
        if session is None:
            raise SessionError("Trusted supplier session required")
        session.require_supplier(supplier_id)
        return self.quote(supplier_id, item_id, quantity, expedited=expedited)

    def quote(self, supplier_id: str, item_id: str, quantity: int, *, expedited: bool = False) -> dict:
        """Internal fact calculator; public entry points enforce identity separately."""
        if type(quantity) is not int or not 1 <= quantity <= 1_000_000:
            raise ValueError("Quantity must be a whole number from 1 to 1000000")
        rows = self.database_client.fetch_all("supplier_quote_inputs", {"supplier_id": supplier_id, "item_id": item_id})
        if not rows:
            raise ValueError("Supplier is not eligible for this item")
        row = rows[0]
        document = self.policy_service.get_supplier_policy(supplier_id, object_name=row["policy_object_name"])
        terms = parse_policy_terms(document)
        product = terms["products"].get(item_id)
        if (not product or product["moq"] != row["minimum_order_quantity"]
                or product["lead_time_days"] != row["lead_time_days"]
                or product["base_price"] != Decimal(str(row["base_price"]))):
            raise PolicyDocumentError("policy_database_terms_mismatch")
        stock = row["available_quantity"]
        reasons = []
        if quantity < product["moq"]:
            reasons.append("below_minimum_order_quantity")
        if stock is None:
            reasons.append("supplier_stock_unknown")
        elif stock < quantity:
            reasons.append("insufficient_supplier_stock")
        unit_price = next((price for low, high, price in product["tiers"]
                           if quantity >= low and (high is None or quantity <= high)), product["base_price"])
        rate, lead = (expedited_terms(supplier_id, item_id, product["lead_time_days"]) if expedited
                      else (Decimal(0), product["lead_time_days"]))
        report = self.inventory_service.get_inventory_risk_report()
        assessment = next((item for key in ("at_risk_items", "not_at_risk_items")
                           for item in report[key] if item["item_id"] == item_id), None)
        if assessment is None:
            reasons.append("retail_demand_unknown")
        elif lead is not None and (Decimal(str(assessment["current_stock"])) -
                                  Decimal(str(assessment["average_daily_demand"])) * lead < 0):
            reasons.append("delivery_after_stockout")
        if lead is None:
            reasons.append("delivery_lead_time_unconfirmed")
        if expedited:
            reasons.append("expedited_surcharge_basis_not_specified")
        money = lambda value: format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")
        subtotal = unit_price * quantity
        return dict(supplier_id=supplier_id, supplier_name=row["supplier_name"], item_id=item_id,
                    quantity=quantity, minimum_order_quantity=product["moq"], available_quantity=stock,
                    currency=terms["currency"], unit=product["unit"], base_price=money(product["base_price"]),
                    unit_price=money(unit_price), discount_amount=money((product["base_price"]-unit_price)*quantity),
                    subtotal=money(subtotal), expedited=expedited, surcharge=None if expedited else "0.00",
                    surcharge_rate_percent=str(rate * 100) if expedited else None,
                    priced_total=None if expedited else money(subtotal), lead_time_days=lead,
                    feasible=not reasons, infeasibility_reasons=reasons,
                    payment_terms=terms["payment_terms"], delivery_terms=terms["delivery_terms"],
                    special_conditions=terms["special_conditions"], policy_sha256=terms["policy_sha256"],
                    policy_source=terms["source"], valid_until=terms["valid_until"],
                    history_quality=assessment["history_quality"] if assessment else None,
                    exclusions=["Unspecified delivery charges, taxes, and unpriced expedited service are excluded; no all-in total is asserted."],
                    confirmation_required=True)

    def compare(self, item_id: str, quantity: int, *, expedited: bool = False) -> dict:
        if type(quantity) is not int or not 1 <= quantity <= 1_000_000:
            raise ValueError("Quantity must be a whole number from 1 to 1000000")
        if not self.database_client.fetch_all("item_details", {"item_id": item_id}):
            raise ItemNotFound("Item not found")
        quotes = []
        for supplier in self.get_suppliers_for_item(item_id):
            try:
                adjusted = max(quantity, int(supplier["minimum_order_quantity"]))
                quotes.append(self.quote(supplier["supplier_id"], item_id, adjusted, expedited=expedited) |
                              {"requested_quantity": quantity, "quantity_adjusted": adjusted != quantity})
            except PolicyDocumentError as exc:
                quotes.append(dict(supplier_id=supplier["supplier_id"], feasible=False,
                                   infeasibility_reasons=["policy_unavailable_or_unsupported"], priced_total=None,
                                   lead_time_days=None))
        quotes.sort(key=lambda q: (not q["feasible"], Decimal(q["priced_total"]) if q["priced_total"] is not None
                                  else Decimal("Infinity"), q["lead_time_days"] or float("inf"), q["supplier_id"]))
        return dict(item_id=item_id, quantity=quantity, suppliers=quotes,
                    recommended_supplier_id=next((q["supplier_id"] for q in quotes if q["feasible"]), None),
                    rationale="Quantity raised to each supplier's MOQ, not rounded to MOQ multiples. Feasible first, then known priced total, lead time, supplier ID. Excluded costs require confirmation.")

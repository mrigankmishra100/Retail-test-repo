"""Adapter boundary for Oracle Enterprise AI Guardrails."""

import logging
from typing import Any

logger = logging.getLogger(__name__)

RETAIL_GUARDRAIL_RULES = (
    "Never invent inventory, suppliers, pricing, or policy terms.",
    "Never commit a purchase or order without manager approval.",
)

SUPPLIER_GUARDRAIL_RULES = (
    "Never access another supplier's data.",
    "Never invent inventory, contract pricing, or delivery capability.",
    "Never send a commercial response without supplier approval.",
)


class EnterpriseAIGuardrailsClient:
    """Validate agent inputs and outputs against role-specific safety rules."""

    def __init__(self, endpoint: str | None = None, enabled: bool = False) -> None:
        self.endpoint = endpoint
        self.enabled = enabled
        # TODO: Initialize the Oracle Enterprise AI Guardrails client lazily.

    async def evaluate(self, content: str, role: str) -> dict[str, Any]:
        """Evaluate content or return a non-networked local placeholder."""
        rules = RETAIL_GUARDRAIL_RULES if role == "retail-manager" else SUPPLIER_GUARDRAIL_RULES
        if not self.enabled or not self.endpoint:
            logger.info("Oracle Enterprise AI Guardrails is not configured")
            return {"status": "not_configured", "allowed": None, "rules": list(rules)}
        # TODO: Call Guardrails for both model inputs and outputs.
        raise NotImplementedError("Oracle Enterprise AI Guardrails integration is pending.")

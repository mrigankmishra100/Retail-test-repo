"""Trusted executed-plan metadata; SQL comes only from our fixed read operations."""
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class QueryEvidence(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    operation: Literal['nl2sql_inventory', 'nl2sql_sales_summary', 'nl2sql_supplier_options']
    parameters: dict[str, str | int | None]
    row_count: int = Field(strict=True, ge=0, le=50)

    @model_validator(mode='after')
    def safe_parameters(self):
        from app.oci.database import READ_OPERATIONS
        if set(self.parameters) != READ_OPERATIONS[self.operation].parameter_names:
            raise ValueError('Unexpected query parameters')
        for name, value in self.parameters.items():
            if name in {'item_id', 'supplier_id'}:
                pattern = r'ITEM[0-9]{3}' if name == 'item_id' else r'SUP[0-9]{3}'
                if value is not None and (not isinstance(value, str) or not re.fullmatch(pattern, value)):
                    raise ValueError('Invalid query identifier')
            elif name in {'page_size', 'days'}:
                if type(value) is not int or not 1 <= value <= (50 if name == 'page_size' else 365):
                    raise ValueError('Invalid query bound')
            elif name == 'sort_by' and value not in {
                'item_id', 'stock_low_to_high', 'stock_high_to_low', 'sales_high_to_low',
                'sales_low_to_high', 'price_low_to_high', 'lead_time_low_to_high', 'availability_high_to_low'}:
                raise ValueError('Invalid query sort')
        return self

    def display(self):
        from app.oci.database import READ_OPERATIONS
        import textwrap
        # Never trust SQL supplied by a model or a remote tool response.
        return {**self.model_dump(), 'sql': textwrap.dedent(READ_OPERATIONS[self.operation].sql).strip()}

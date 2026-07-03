"""In-memory CRM store, loaded from data/customers.json.

Reads are plain dict lookups; writes (processed refunds, recorded denials)
mutate the in-memory copy only, so a server restart or reset() returns the
demo data to its original state.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.config import CUSTOMERS_PATH

_customers: list[dict] = []
decisions: list[dict] = []


def reset() -> None:
    """(Re)load customer data from disk and clear recorded decisions."""
    global _customers
    _customers = json.loads(CUSTOMERS_PATH.read_text())["customers"]
    decisions.clear()


def all_customers() -> list[dict]:
    return _customers


def find_customer_by_email(email: str) -> dict | None:
    email = email.strip().lower()
    return next((c for c in _customers if c["email"].lower() == email), None)


def get_customer(customer_id: str) -> dict | None:
    return next((c for c in _customers if c["id"] == customer_id), None)


def get_order(customer: dict, order_id: str) -> dict | None:
    return next((o for o in customer["orders"] if o["id"] == order_id), None)


def get_item(order: dict, item_id: str) -> dict | None:
    return next((i for i in order["items"] if i["id"] == item_id), None)


def record_decision(decision: str, payload: dict) -> dict:
    """Append an approved/denied/escalated decision to the audit log.

    `payload` is a verdict dict whose own `kind` field is preserved; the
    decision type lives under the distinct `decision` key.
    """
    record = {
        **payload,
        "decision": decision,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    decisions.append(record)
    return record


def apply_refund(customer: dict, item: dict) -> None:
    """Execute an approved refund against the in-memory CRM state."""
    item["refunded"] = True
    customer["refunds_past_year"] = customer.get("refunds_past_year", 0) + 1


reset()

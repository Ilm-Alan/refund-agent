"""In-memory CRM store, loaded from data/customers.json.

Reads are plain dict lookups; writes (processed refunds, recorded denials)
mutate the in-memory copy only, so a server restart or reset() returns the
demo data to its original state.

Order dates on disk are written relative to the file's seed_date and are
kept shifted so that seed_date lands on today, re-anchored whenever the date
changes, even without a restart: every scenario keeps the day counts it was
designed with (delivered 8 days ago, 45 days ago, ...) instead of aging out
of the refund window.
"""

from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta, timezone

from app.config import CUSTOMERS_PATH

_customers: list[dict] = []
decisions: list[dict] = []

# The day seed_date currently maps to. Sync endpoints read the store from a
# worker thread, so anchoring happens under a lock.
_anchored_on = date.min
_lock = threading.Lock()


def reset() -> None:
    """(Re)load customer data from disk and clear recorded decisions."""
    global _customers, _anchored_on
    data = json.loads(CUSTOMERS_PATH.read_text())
    with _lock:
        _customers = data["customers"]
        _anchored_on = date.fromisoformat(data["seed_date"])
        decisions.clear()
        _follow_today()


def _follow_today() -> None:
    """Shift order dates so seed_date lands on today. Caller holds _lock."""
    global _anchored_on
    today = date.today()
    offset = today - _anchored_on
    if not offset:
        return
    for customer in _customers:
        for order in customer["orders"]:
            order["date"] = _shift(order["date"], offset)
            order["delivered"] = _shift(order["delivered"], offset)
    _anchored_on = today


def _shift(iso_date: str | None, offset: timedelta) -> str | None:
    if iso_date is None:
        return None
    return (date.fromisoformat(iso_date) + offset).isoformat()


def all_customers() -> list[dict]:
    with _lock:
        _follow_today()
        return _customers


def find_customer_by_email(email: str) -> dict | None:
    email = email.strip().lower()
    return next((c for c in all_customers() if c["email"].lower() == email), None)


def get_customer(customer_id: str) -> dict | None:
    return next((c for c in all_customers() if c["id"] == customer_id), None)


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


def apply_refund(customer: dict, item: dict, reason: str) -> None:
    """Execute an approved refund against the in-memory CRM state."""
    item["refunded"] = True
    item["refund_reason"] = reason
    customer["refunds_past_year"] = customer.get("refunds_past_year", 0) + 1


reset()

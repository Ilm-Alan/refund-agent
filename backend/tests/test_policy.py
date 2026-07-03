"""Policy engine tests, run against the real CRM fixtures in data/.

Each case pins one rule of refund_policy.md to a concrete customer, so the
data set is guaranteed to exercise the whole policy surface. Dates are fixed
so results never drift as the calendar moves.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from app.agent.policy import Verdict, check_eligibility

TODAY = date(2026, 7, 2)
DATA = json.loads(
    (Path(__file__).resolve().parent.parent / "data" / "customers.json").read_text()
)


def fixture(customer_id: str, order_id: str, sku: str) -> tuple[dict, dict, dict]:
    customer = next(c for c in DATA["customers"] if c["id"] == customer_id)
    order = next(o for o in customer["orders"] if o["id"] == order_id)
    item = next(i for i in order["items"] if i["id"] == sku)
    return customer, order, item


def check(customer_id, order_id, sku, reason, **kwargs) -> Verdict:
    c, o, i = fixture(customer_id, order_id, sku)
    return check_eligibility(c, o, i, reason, today=TODAY, **kwargs)


def test_full_refund_within_window():
    v = check("cust_001", "ORD-1024", "SKU-4411", "defective")
    assert v.eligible and v.kind == "full"
    assert v.refund_amount == 89.99
    assert v.rule_ids == ["R1", "R3"]


def test_denied_outside_window():
    v = check("cust_002", "ORD-0937", "SKU-5102", "changed_mind")
    assert not v.eligible and v.kind == "denied"
    assert v.rule_ids == ["R1"]
    assert "45 days" in v.summary


def test_vip_extension_saves_late_return():
    v = check("cust_003", "ORD-0952", "SKU-7720", "changed_mind")
    assert v.eligible and v.kind == "full"
    assert v.refund_amount == 149.00
    assert "R5" in v.rule_ids


def test_vip_extension_capped_by_price():
    v = check("cust_004", "ORD-0910", "SKU-8801", "defective")
    assert not v.eligible and v.kind == "denied"
    assert v.rule_ids == ["R1", "R5"]


def test_fraud_flag_escalates():
    v = check("cust_005", "ORD-1067", "SKU-4470", "defective")
    assert not v.eligible and v.kind == "escalate"
    assert v.rule_ids == ["R6"]


def test_refund_frequency_cap():
    v = check("cust_006", "ORD-1043", "SKU-3319", "defective")
    assert not v.eligible and v.rule_ids == ["R4"]


def test_gift_card_never_refundable():
    v = check("cust_007", "ORD-1077", "SKU-9001", "changed_mind")
    assert not v.eligible and v.rule_ids == ["R2"]


def test_perishable_never_refundable():
    v = check("cust_008", "ORD-1090", "SKU-7104", "not_as_described")
    assert not v.eligible and v.rule_ids == ["R2"]


def test_final_sale_never_refundable():
    v = check("cust_009", "ORD-1055", "SKU-6640", "defective")
    assert not v.eligible and v.rule_ids == ["R2"]


def test_opened_electronics_restocking_fee():
    v = check("cust_010", "ORD-1061", "SKU-4482", "changed_mind", opened=True)
    assert v.eligible and v.kind == "partial"
    assert v.refund_amount == 220.15
    assert v.rule_ids == ["R1", "R3"]


def test_unopened_changed_mind_full_refund():
    v = check("cust_010", "ORD-1061", "SKU-4482", "changed_mind", opened=False)
    assert v.eligible and v.kind == "full"
    assert v.refund_amount == 259.00


def test_undelivered_order_not_refundable():
    v = check("cust_011", "ORD-1095", "SKU-5150", "changed_mind")
    assert not v.eligible and v.rule_ids == ["R1"]
    assert "cancellation" in v.summary


def test_digital_item_denied_but_physical_item_on_same_order_allowed():
    ebook = check("cust_012", "ORD-1032", "SKU-9010", "changed_mind")
    scarf = check("cust_012", "ORD-1032", "SKU-7735", "changed_mind")
    assert not ebook.eligible and ebook.rule_ids == ["R2"]
    assert scarf.eligible and scarf.refund_amount == 85.00


def test_already_refunded_item_denied():
    c, o, i = fixture("cust_001", "ORD-1024", "SKU-4411")
    v = check_eligibility(c, o, {**i, "refunded": True}, "defective", today=TODAY)
    assert not v.eligible and v.rule_ids == ["R8"]


def test_unknown_reason_rejected():
    with pytest.raises(ValueError):
        check("cust_001", "ORD-1024", "SKU-4411", "i-deserve-it")


def test_every_verdict_carries_citations():
    v = check("cust_002", "ORD-0937", "SKU-5102", "changed_mind")
    assert v.citations and all("text" in c and "rule_id" in c for c in v.citations)
    assert v.as_dict()["citations"] == v.citations

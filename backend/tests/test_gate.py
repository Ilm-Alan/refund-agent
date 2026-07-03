"""Execution-time gate tests.

check_eligibility answers a question; process_refund enforces one. These
tests exercise the enforcement path in the dispatcher: verdicts are re-ruled
at execution time against current CRM state, so an "eligible" answer earlier
in a conversation does not survive a state change.
"""

import json

import pytest

from app import store
from app.agent.tools import execute_tool


@pytest.fixture(autouse=True)
def fresh_store():
    store.reset()
    yield
    store.reset()


def call(name, **tool_input):
    events = []
    result, is_error = execute_tool(
        name, tool_input, lambda kind, payload: events.append((kind, payload))
    )
    return json.loads(result), is_error, events


def test_refund_executes_and_mutates_state():
    result, is_error, events = call(
        "process_refund",
        customer_id="cust_012", order_id="ORD-1032", item_id="SKU-7735",
        reason="changed_mind",
    )
    assert not is_error
    assert result["amount"] == 85.00
    farida = store.get_customer("cust_012")
    assert farida["refunds_past_year"] == 3
    assert any(kind == "refund_processed" for kind, _ in events)


def test_earlier_eligibility_does_not_survive_crossing_the_cap():
    # Both items are eligible before anything is processed.
    napkins_before, is_error, _ = call(
        "check_refund_eligibility",
        customer_id="cust_012", order_id="ORD-1032", item_id="SKU-7736",
        reason="changed_mind",
    )
    assert not is_error and napkins_before["eligible"]

    # The first refund crosses the 3-per-year cap (R4)...
    _, is_error, _ = call(
        "process_refund",
        customer_id="cust_012", order_id="ORD-1032", item_id="SKU-7735",
        reason="changed_mind",
    )
    assert not is_error

    # ...so executing the second is refused by the gate, despite the
    # earlier "eligible" verdict.
    result, is_error, events = call(
        "process_refund",
        customer_id="cust_012", order_id="ORD-1032", item_id="SKU-7736",
        reason="changed_mind",
    )
    assert is_error
    assert "refused by the policy engine" in result["error"]
    assert any(kind == "gate_refusal" for kind, _ in events)
    farida = store.get_customer("cust_012")
    napkins = store.get_item(store.get_order(farida, "ORD-1032"), "SKU-7736")
    assert not napkins.get("refunded")
    assert farida["refunds_past_year"] == 3


def test_double_refund_of_same_item_refused():
    _, is_error, _ = call(
        "process_refund",
        customer_id="cust_001", order_id="ORD-1024", item_id="SKU-4411",
        reason="defective",
    )
    assert not is_error
    result, is_error, events = call(
        "process_refund",
        customer_id="cust_001", order_id="ORD-1024", item_id="SKU-4411",
        reason="defective",
    )
    assert is_error
    assert "refused by the policy engine" in result["error"]
    maya = store.get_customer("cust_001")
    assert maya["refunds_past_year"] == 1


def test_gate_refusal_is_recorded_for_audit():
    call(
        "process_refund",
        customer_id="cust_002", order_id="ORD-0937", item_id="SKU-5102",
        reason="changed_mind",
    )
    assert [d["decision"] for d in store.decisions] == ["refused_by_gate"]
    assert store.decisions[0]["rule_ids"] == ["R1"]

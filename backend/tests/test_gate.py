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


def test_seed_scenarios_keep_their_day_counts():
    # Order dates are shifted on load, so the README scenarios hold on any
    # calendar date: Derek's keyboard is always 45 days past delivery.
    result, is_error, _ = call(
        "check_refund_eligibility",
        customer_id="cust_002", order_id="ORD-0937", item_id="SKU-5102",
        reason="changed_mind",
    )
    assert not is_error and not result["eligible"]
    assert "delivered 45 days ago" in result["summary"]


def test_non_boolean_opened_is_rejected_not_coerced():
    # A string "false" must not be read as truthy and cost the customer a
    # restocking fee.
    result, is_error, _ = call(
        "process_refund",
        customer_id="cust_010", order_id="ORD-1061", item_id="SKU-4482",
        reason="changed_mind", opened="false",
    )
    assert is_error and "'opened'" in result["error"]
    rosa = store.get_customer("cust_010")
    assert rosa["refunds_past_year"] == 0


def test_shipping_refunded_with_last_item_when_all_defective():
    first, is_error, _ = call(
        "process_refund",
        customer_id="cust_001", order_id="ORD-1024", item_id="SKU-4411",
        reason="defective",
    )
    assert not is_error and first["amount"] == 89.99
    last, is_error, _ = call(
        "process_refund",
        customer_id="cust_001", order_id="ORD-1024", item_id="SKU-2210",
        reason="not_as_described",
    )
    assert not is_error and last["amount"] == 19.49  # 12.50 + 6.99 shipping


def test_shipping_kept_when_any_item_refunded_for_other_reason():
    call(
        "process_refund",
        customer_id="cust_001", order_id="ORD-1024", item_id="SKU-4411",
        reason="changed_mind",
    )
    last, is_error, _ = call(
        "process_refund",
        customer_id="cust_001", order_id="ORD-1024", item_id="SKU-2210",
        reason="defective",
    )
    assert not is_error and last["amount"] == 12.50


def test_arguments_outside_the_schema_are_rejected():
    # The model cannot pass an amount; an attempt must fail loudly rather
    # than be silently ignored while the refund goes through.
    result, is_error, events = call(
        "process_refund",
        customer_id="cust_001", order_id="ORD-1024", item_id="SKU-4411",
        reason="defective", amount=500.0,
    )
    assert is_error and "'amount'" in result["error"]
    assert not any(kind == "refund_processed" for kind, _ in events)
    assert store.get_customer("cust_001")["refunds_past_year"] == 0

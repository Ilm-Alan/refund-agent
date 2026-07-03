"""Tool definitions and the validating dispatcher for the refund agent.

Schemas are tight (additionalProperties false, explicit required lists), but
schema enforcement varies across Anthropic-compatible providers, so the
dispatcher re-validates every input server-side: unknown or mismatched IDs
come back as descriptive error results the model can recover from.

The hard gate lives in policy.py, not in the prompt: process_refund re-runs
the eligibility check and computes the amount itself (the model cannot pass
one), so the model cannot be talked into a refund the policy forbids.

Two audiences, two payloads: the full policy verdict (including fraud-freeze
reasoning) is published to the event bus for the admin panel; the model only
receives what it is allowed to tell the customer.
"""

from __future__ import annotations

import json
from typing import Callable

from app import store
from app.agent.policy import VALID_REASONS, check_eligibility

Emit = Callable[[str, dict], None]

_REASON_SCHEMA = {
    "type": "string",
    "enum": sorted(VALID_REASONS),
    "description": "The customer's stated reason for the refund request.",
}

TOOLS = [
    {
        "name": "lookup_customer",
        "description": "Look up a customer by the email address they provide. "
        "Returns their profile and a summary of their orders. Always call "
        "this first; all other tools need the customer_id it returns.",
        "input_schema": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Customer email address."}
            },
            "required": ["email"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_order",
        "description": "Fetch full detail for one order, including items with "
        "SKUs and prices. The order must belong to the given customer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "order_id": {"type": "string"},
            },
            "required": ["customer_id", "order_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "check_refund_eligibility",
        "description": "Ask the policy engine whether one item on an order is "
        "refundable, and for how much. Returns the verdict with the policy "
        "rules it cites. Call this before promising anything to the customer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "order_id": {"type": "string"},
                "item_id": {"type": "string", "description": "The item SKU."},
                "reason": _REASON_SCHEMA,
                "opened": {
                    "type": "boolean",
                    "description": "Whether the customer says the item has "
                    "been opened or used. Ask if unknown; defaults to false.",
                },
            },
            "required": ["customer_id", "order_id", "item_id", "reason"],
            "additionalProperties": False,
        },
    },
    {
        "name": "process_refund",
        "description": "Execute a refund for one item. The policy engine "
        "re-validates eligibility and computes the amount server-side; if the "
        "item is not eligible this fails and no refund is issued. Only call "
        "after the customer confirms they want the refund.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "order_id": {"type": "string"},
                "item_id": {"type": "string", "description": "The item SKU."},
                "reason": _REASON_SCHEMA,
                "opened": {"type": "boolean"},
            },
            "required": ["customer_id", "order_id", "item_id", "reason"],
            "additionalProperties": False,
        },
    },
    {
        "name": "deny_refund",
        "description": "Record a final denial for one item after the policy "
        "engine has ruled it ineligible. Use this to close out a request you "
        "are declining; the denial and its policy citations go to the audit "
        "log.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "order_id": {"type": "string"},
                "item_id": {"type": "string", "description": "The item SKU."},
                "reason": _REASON_SCHEMA,
            },
            "required": ["customer_id", "order_id", "item_id", "reason"],
            "additionalProperties": False,
        },
    },
]


class ToolError(Exception):
    """Validation failure returned to the model as an error tool_result."""


def _require_str(tool_input: dict, key: str) -> str:
    value = tool_input.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"missing or invalid '{key}': expected a non-empty string")
    return value.strip()


def _resolve(tool_input: dict, with_item: bool = True) -> tuple[dict, dict, dict | None]:
    """Validate customer/order/item IDs against the CRM and return the records."""
    customer_id = _require_str(tool_input, "customer_id")
    order_id = _require_str(tool_input, "order_id")
    customer = store.get_customer(customer_id)
    if customer is None:
        raise ToolError(
            f"unknown customer_id {customer_id!r}; use the customer_id "
            "returned by lookup_customer"
        )
    order = store.get_order(customer, order_id)
    if order is None:
        known = [o["id"] for o in customer["orders"]]
        raise ToolError(
            f"order {order_id!r} not found for customer {customer_id}; "
            f"their orders are: {', '.join(known)}"
        )
    item = None
    if with_item:
        item_id = _require_str(tool_input, "item_id")
        item = store.get_item(order, item_id)
        if item is None:
            known = [i["id"] for i in order["items"]]
            raise ToolError(
                f"item {item_id!r} not found on order {order_id}; "
                f"its items are: {', '.join(known)}"
            )
    return customer, order, item


def _reason(tool_input: dict) -> str:
    reason = _require_str(tool_input, "reason")
    if reason not in VALID_REASONS:
        raise ToolError(
            f"invalid reason {reason!r}; expected one of {sorted(VALID_REASONS)}"
        )
    return reason


def _customer_view(customer: dict) -> dict:
    """Profile as the model may see it: no fraud flag, summarized orders."""
    return {
        "customer_id": customer["id"],
        "name": customer["name"],
        "email": customer["email"],
        "joined": customer["joined"],
        "vip": customer["vip"],
        "refunds_past_year": customer["refunds_past_year"],
        "orders": [
            {
                "order_id": o["id"],
                "date": o["date"],
                "status": o["status"],
                "delivered": o["delivered"],
                "items": [
                    {"item_id": i["id"], "name": i["name"], "price": i["price"]}
                    for i in o["items"]
                ],
            }
            for o in customer["orders"]
        ],
    }


def _model_safe_verdict(verdict_dict: dict) -> dict:
    """Strip internals the model must not repeat to the customer.

    Fraud-freeze escalations (R6) reach the model only as 'manual review
    required'; the full verdict still goes to the admin event bus.
    """
    if verdict_dict["kind"] != "escalate":
        return verdict_dict
    return {
        "eligible": False,
        "kind": "escalate",
        "refund_amount": None,
        "summary": "This request cannot be completed automatically and has "
        "been routed to a specialist for manual review. Tell the customer a "
        "specialist will follow up by email within 1 business day. Do not "
        "speculate about the reason.",
    }


def execute_tool(name: str, tool_input: dict, emit: Emit) -> tuple[str, bool]:
    """Run one tool call. Returns (json_result, is_error)."""
    try:
        result = _dispatch(name, tool_input, emit)
        return json.dumps(result), False
    except ToolError as exc:
        return json.dumps({"error": str(exc)}), True


def _dispatch(name: str, tool_input: dict, emit: Emit) -> dict:
    if name == "lookup_customer":
        email = _require_str(tool_input, "email")
        customer = store.find_customer_by_email(email)
        if customer is None:
            raise ToolError(
                f"no customer found for email {email!r}; ask the customer to "
                "confirm the email on their account"
            )
        return _customer_view(customer)

    if name == "get_order":
        customer, order, _ = _resolve(tool_input, with_item=False)
        return {"customer_id": customer["id"], **order}

    if name == "check_refund_eligibility":
        customer, order, item = _resolve(tool_input)
        verdict = check_eligibility(
            customer, order, item, _reason(tool_input),
            opened=bool(tool_input.get("opened", False)),
        )
        full = {
            "customer_id": customer["id"],
            "order_id": order["id"],
            "item_id": item["id"],
            **verdict.as_dict(),
        }
        emit("policy_verdict", full)
        return _model_safe_verdict(full)

    if name == "process_refund":
        customer, order, item = _resolve(tool_input)
        verdict = check_eligibility(
            customer, order, item, _reason(tool_input),
            opened=bool(tool_input.get("opened", False)),
        )
        full = {
            "customer_id": customer["id"],
            "order_id": order["id"],
            "item_id": item["id"],
            **verdict.as_dict(),
        }
        emit("policy_verdict", full)
        if not verdict.eligible:
            # The gate. Reached only if the model tries to refund an
            # ineligible item; the error result forces it to hold the line.
            store.record_decision("refused_by_gate", full)
            emit("gate_refusal", full)
            safe = _model_safe_verdict(full)
            raise ToolError(
                "refund refused by the policy engine: "
                + safe["summary"]
                + " No refund has been issued."
            )
        store.apply_refund(customer, item)
        decision = store.record_decision("refund_processed", full)
        emit("refund_processed", decision)
        return {
            "status": "refunded",
            "item": item["name"],
            "amount": verdict.refund_amount,
            "kind": verdict.kind,
            "method": "original payment method, 5-10 business days (rule R7)",
        }

    if name == "deny_refund":
        customer, order, item = _resolve(tool_input)
        verdict = check_eligibility(customer, order, item, _reason(tool_input))
        full = {
            "customer_id": customer["id"],
            "order_id": order["id"],
            "item_id": item["id"],
            **verdict.as_dict(),
        }
        if verdict.eligible:
            raise ToolError(
                "the policy engine finds this item ELIGIBLE for a refund; "
                "deny_refund is only for ineligible requests. Re-check "
                "eligibility and offer the refund instead."
            )
        decision = store.record_decision("refund_denied", full)
        emit("refund_denied", decision)
        return _model_safe_verdict(
            {**full, "status": "denial recorded", "kind": verdict.kind}
        )

    raise ToolError(f"unknown tool {name!r}")

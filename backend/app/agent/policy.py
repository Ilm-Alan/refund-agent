"""Deterministic refund policy engine.

Pure functions over data/refund_policy.md's rules: given an order item
and a claimed reason, return an eligibility verdict with the specific
rule(s) applied (window, item category, condition, prior-refund count,
fraud flags, partial-refund rules).

Deliberately NOT the LLM's job: the model gathers context, explains
decisions, and drives the conversation; this module decides. Every
verdict is published to the event bus so the admin panel shows exactly
which rule fired.
"""

# To implement: check_eligibility(order, item, reason) -> Verdict

"""Tool definitions and handlers for the refund agent.

Planned tools (tight schemas: additionalProperties false, explicit
required lists; the dispatcher also validates every input server-side,
since schema enforcement varies across Anthropic-compatible providers):

    lookup_customer(email)              -> profile + order summaries
    get_order(order_id)                 -> full order detail
    check_refund_eligibility(order_id, item_id, reason)
                                        -> policy engine verdict + cited rules
    process_refund(order_id, item_id, amount)
                                        -> executes ONLY if the policy engine
                                           approved; re-checks server-side
    deny_refund(order_id, item_id, reason, policy_citation)
                                        -> records the denial with the rule cited

The hard gate lives in policy.py, not in the prompt: process_refund
re-validates eligibility before executing, so the model cannot be
talked into a refund the policy forbids. That is the "holding the
line" mechanism the demo shows.
"""

# To implement: TOOLS list (schemas) + execute_tool(name, input) dispatcher

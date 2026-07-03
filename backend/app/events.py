"""In-memory event bus.

Every agent step (model text, tool call, tool result, policy decision,
error, retry) is published here. The admin dashboard subscribes to the
full stream; the customer chat receives only customer-facing events.
"""

# To implement: an asyncio-based pub/sub with per-subscriber queues and
# a typed AgentEvent model (kind, session_id, payload, timestamp).

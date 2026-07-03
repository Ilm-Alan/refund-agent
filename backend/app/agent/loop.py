"""The agent loop: Claude + tool calls, run manually so every step is observable.

A manual loop (rather than the SDK tool runner) is deliberate: each model
response and tool result is published to the event bus before the next
API call, which is what feeds the admin dashboard's live reasoning log.

Shape (see the Anthropic docs for the canonical pattern):

    while True:
        response = client.messages.create(model=MODEL, max_tokens=MAX_TOKENS,
                                          system=SYSTEM_PROMPT, tools=TOOLS,
                                          messages=messages)
        if response.stop_reason != "tool_use":
            break
        # publish tool_use events, execute tools, append tool_result blocks
        # (all results for one assistant turn go back in ONE user message)

Error handling: retry rate limits / 5xx with backoff (publish a `retry`
event so it shows in the admin panel); tool failures return
is_error=True tool_results so the model can recover; cap iterations at
MAX_AGENT_STEPS.
"""

# To implement: run_turn(session, user_message) -> async generator of events

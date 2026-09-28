"""Agent loop tests, with a scripted model standing in for the provider.

The Messages API rejects any history where an assistant tool_use block is not
answered by a tool_result in the next user message, so a turn that ends or
fails partway must never leave one behind: the session would be unusable for
every later turn.
"""

import asyncio
import json

import pytest
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage
from fastapi.testclient import TestClient

from app import main, store
from app.agent import loop


class ScriptedModel:
    """Replays a fixed list of responses (or exceptions) in order."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.requests: list[list] = []
        self.messages = self

    async def create(self, **kwargs):
        self.requests.append(list(kwargs["messages"]))
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def response(stop_reason: str, *content) -> Message:
    return Message(
        id="msg_test",
        type="message",
        role="assistant",
        model="scripted",
        content=list(content),
        stop_reason=stop_reason,
        stop_sequence=None,
        usage=Usage(input_tokens=1, output_tokens=1),
    )


def lookup(tool_id: str = "toolu_1") -> ToolUseBlock:
    return ToolUseBlock(
        id=tool_id, type="tool_use", name="lookup_customer",
        input={"email": "maya.chen@example.com"},
    )


def text(value: str) -> TextBlock:
    return TextBlock(type="text", text=value)


def assert_valid_history(messages: list) -> None:
    """Every tool_use is answered by a tool_result in the next message."""
    for i, message in enumerate(messages):
        if message["role"] != "assistant" or isinstance(message["content"], str):
            continue
        tool_ids = {b.id for b in message["content"] if b.type == "tool_use"}
        if not tool_ids:
            continue
        assert i + 1 < len(messages), "tool_use left unanswered at end of history"
        answered = {
            r["tool_use_id"]
            for r in messages[i + 1]["content"]
            if isinstance(r, dict) and r.get("type") == "tool_result"
        }
        assert tool_ids <= answered


@pytest.fixture(autouse=True)
def fresh_state():
    store.reset()
    main._sessions.clear()
    yield
    store.reset()
    main._sessions.clear()


def use_model(monkeypatch, model: ScriptedModel) -> None:
    monkeypatch.setattr(loop, "get_client", lambda: model)


def chat(client: TestClient, session_id: str, message: str) -> list[dict]:
    body = client.post("/api/chat", json={"session_id": session_id, "message": message}).text
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


def test_failed_turn_after_tool_call_leaves_session_usable(monkeypatch):
    model = ScriptedModel(
        response("tool_use", lookup()),
        RuntimeError("provider exploded"),
        response("end_turn", text("How can I help?")),
    )
    use_model(monkeypatch, model)
    client = TestClient(main.app)

    events = chat(client, "s1", "hi, maya.chen@example.com")
    assert events[-2]["kind"] == "reply" and "went wrong" in events[-2]["text"]

    events = chat(client, "s1", "hello again")
    assert events[-2] == {"kind": "reply", "text": "How can I help?"}
    retried_history = model.requests[-1]
    assert_valid_history(retried_history)
    assert retried_history[-1] == {"role": "user", "content": "hello again"}


def test_tool_use_cut_off_by_max_tokens_is_still_answered(monkeypatch):
    model = ScriptedModel(
        response("max_tokens", lookup()),
        response("end_turn", text("Found your account.")),
    )
    use_model(monkeypatch, model)
    messages = [{"role": "user", "content": "maya.chen@example.com"}]

    reply = asyncio.run(loop.run_turn(messages, lambda kind, payload: None))

    assert reply == "Found your account."
    assert_valid_history(messages)
    assert_valid_history(model.requests[-1])


def test_text_sent_alongside_a_tool_call_reaches_the_customer(monkeypatch):
    # Models often explain a denial and record it in the same response; the
    # explanation must not be lost just because a tool call came with it.
    deny = ToolUseBlock(
        id="toolu_2", type="tool_use", name="deny_refund",
        input={
            "customer_id": "cust_002", "order_id": "ORD-0937",
            "item_id": "SKU-5102", "reason": "changed_mind",
        },
    )
    model = ScriptedModel(
        response("tool_use", text("That order is outside the 30-day window (R1)."), deny),
        response("end_turn", text("Your request has been documented.")),
    )
    use_model(monkeypatch, model)
    messages = [{"role": "user", "content": "refund my keyboard"}]

    reply = asyncio.run(loop.run_turn(messages, lambda kind, payload: None))

    assert "outside the 30-day window (R1)" in reply
    assert reply.endswith("Your request has been documented.")

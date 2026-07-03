"""In-memory event bus.

Every agent step (model text, tool call, tool result, policy verdict,
error, retry) is published here. The admin dashboard subscribes to the
full stream; the customer chat stream is built separately in main.py and
carries only customer-safe progress events, never raw verdicts.
"""

from __future__ import annotations

import asyncio
import itertools
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

HISTORY_SIZE = 500


@dataclass
class AgentEvent:
    seq: int
    kind: str
    session_id: str
    at: str
    payload: dict

    def as_dict(self) -> dict:
        return asdict(self)


class EventBus:
    """Asyncio pub/sub with per-subscriber queues and bounded replay history.

    publish() is synchronous and must be called from the event loop thread;
    it never blocks (slow subscribers get events dropped rather than stalling
    the agent loop).
    """

    def __init__(self) -> None:
        self._seq = itertools.count(1)
        self._subscribers: set[asyncio.Queue] = set()
        self._history: deque[AgentEvent] = deque(maxlen=HISTORY_SIZE)

    def publish(self, kind: str, session_id: str, payload: dict) -> AgentEvent:
        event = AgentEvent(
            seq=next(self._seq),
            kind=kind,
            session_id=session_id,
            at=datetime.now(timezone.utc).isoformat(),
            payload=payload,
        )
        self._history.append(event)
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
        return event

    def subscribe(self, replay: bool = True) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        if replay:
            for event in self._history:
                queue.put_nowait(event)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)


bus = EventBus()

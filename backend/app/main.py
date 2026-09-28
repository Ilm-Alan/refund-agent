"""FastAPI entry point for the refund agent.

Run with:  uv run uvicorn app.main:app --reload --port 8000 --timeout-graceful-shutdown 5

The shutdown timeout matters: the admin SSE stream never ends on its own, so
without it uvicorn waits forever for that connection on reload or stop.

Two SSE streams with different audiences:
- POST /api/chat streams one agent turn to the customer UI: progress labels
  and the final reply only, never tool payloads or verdict internals.
- GET /api/events is the admin firehose: every model step, tool call and
  result, policy verdict with citations, retry, and error.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import store, voice
from app.agent.loop import run_turn
from app.config import POLICY_PATH
from app.events import bus

app = FastAPI(title="Refund Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Conversation history per chat session, in memory.
_sessions: dict[str, list] = {}

# The turn running for each session. A session takes one turn at a time (a
# second would interleave history), and holding the task here keeps it
# running to completion even if the customer disconnects mid-turn.
_active_turns: dict[str, asyncio.Task] = {}

# Customer-safe progress labels; anything not listed here stays admin-only.
_PROGRESS_LABELS = {
    "lookup_customer": "Looking up your account",
    "get_order": "Checking your order",
    "check_refund_eligibility": "Checking this against our refund policy",
    "process_refund": "Processing your refund",
    "deny_refund": "Recording the decision",
}


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=4000)


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


def _start_turn(session_id: str, text: str, emit, where: str, extra: dict) -> asyncio.Task:
    """Start one customer turn in the background, or 409 if one is running."""
    if session_id in _active_turns:
        raise HTTPException(
            status_code=409, detail="a reply is still in progress for this conversation"
        )
    task = asyncio.create_task(_run_customer_turn(session_id, text, emit, where, extra))
    _active_turns[session_id] = task
    task.add_done_callback(lambda _: _active_turns.pop(session_id, None))
    return task


async def _run_customer_turn(session_id: str, text: str, emit, where: str, extra: dict) -> str:
    """One turn end to end: history, agent loop, rollback, and the trace."""
    messages = _sessions.setdefault(session_id, [])
    turn_start = len(messages)
    messages.append({"role": "user", "content": text})
    bus.publish("customer_message", session_id, {"text": text, **extra})
    try:
        reply = await run_turn(messages, emit)
    except Exception as exc:  # surfaced to admin stream by the loop already
        reply = (
            "I'm sorry, something went wrong on our side. "
            "Please try again in a moment."
        )
        bus.publish("error", session_id, {"where": where, "error": str(exc)})
        # Drop the whole failed turn (the customer message and any tool
        # round trips) so history stays consistent for a retry.
        del messages[turn_start:]
    bus.publish("agent_reply", session_id, {"text": reply, **extra})
    return reply


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    """Run one agent turn, streaming customer-safe progress then the reply."""
    progress: asyncio.Queue = asyncio.Queue()

    def emit(kind: str, payload: dict) -> None:
        bus.publish(kind, req.session_id, payload)
        if kind == "tool_call" and payload["tool"] in _PROGRESS_LABELS:
            progress.put_nowait({"kind": "working", "label": _PROGRESS_LABELS[payload["tool"]]})
        elif kind == "retry":
            progress.put_nowait({"kind": "working", "label": "Reconnecting, one moment"})

    # Started here rather than inside the stream, so the turn finishes and
    # reaches the trace even if the customer's connection drops.
    task = _start_turn(req.session_id, req.message, emit, where="chat", extra={})

    async def stream():
        while True:
            queue_read = asyncio.create_task(progress.get())
            done, _ = await asyncio.wait(
                {task, queue_read}, return_when=asyncio.FIRST_COMPLETED
            )
            if queue_read in done:
                yield _sse(queue_read.result())
                continue
            queue_read.cancel()
            break
        # Flush any progress events that raced with completion.
        while not progress.empty():
            yield _sse(progress.get_nowait())
        yield _sse({"kind": "reply", "text": task.result()})
        yield _sse({"kind": "done"})

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/config")
def config() -> dict:
    """Feature flags the frontend needs before rendering."""
    return {"voice": voice.configured()}


@app.post("/api/voice")
async def voice_turn(
    session_id: str = Form(min_length=1, max_length=64),
    audio: UploadFile = File(),
) -> dict:
    """One spoken turn: transcribe, run the same agent loop, speak the reply."""
    if not voice.configured():
        raise HTTPException(status_code=503, detail="voice is not configured")
    recording = await audio.read()
    if len(recording) > 10_000_000:
        raise HTTPException(status_code=413, detail="recording too large")

    def emit(kind: str, payload: dict) -> None:
        bus.publish(kind, session_id, payload)

    try:
        transcript = await voice.transcribe(recording, audio.filename or "audio.webm")
    except Exception as exc:
        bus.publish("error", session_id, {"where": "stt", "error": str(exc)})
        raise HTTPException(status_code=502, detail="transcription failed") from exc
    if not transcript:
        raise HTTPException(status_code=422, detail="no speech detected")

    task = _start_turn(
        session_id, transcript, emit, where="voice_chat", extra={"channel": "voice"}
    )
    # Shielded so a dropped request does not cancel the turn itself.
    reply = await asyncio.shield(task)

    audio_b64 = None
    try:
        audio_b64 = base64.b64encode(await voice.synthesize(reply)).decode()
    except Exception as exc:
        # Reply still reaches the customer as text; the failure is on the trace.
        bus.publish("error", session_id, {"where": "tts", "error": str(exc)})
    return {"transcript": transcript, "reply": reply, "audio": audio_b64}


@app.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    """Admin firehose: replay recent history, then stream live events."""

    async def stream():
        queue = bus.subscribe(replay=True)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield _sse(event.as_dict())
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/customers")
def customers() -> dict:
    """Full CRM state for the admin dashboard, including live refund counts."""
    return {"customers": store.all_customers(), "decisions": store.decisions}


@app.get("/api/policy")
def policy() -> PlainTextResponse:
    return PlainTextResponse(POLICY_PATH.read_text())


@app.post("/api/reset")
def reset() -> dict:
    """Restore the CRM to its on-disk state and clear chat sessions."""
    store.reset()
    _sessions.clear()
    bus.publish("demo_reset", "admin", {})
    return {"status": "reset"}


# When a built frontend is present (the deployed container, or a local
# `npm run build`), serve it from the same origin so the relative /api and
# SSE paths need no proxy. Mounted last so every API route wins first.
_dist = Path(os.environ.get("WEB_DIST", str(Path(__file__).resolve().parents[2] / "frontend" / "dist")))
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="web")

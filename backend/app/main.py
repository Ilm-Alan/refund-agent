"""FastAPI entry point for the refund agent.

Run with:  uv run uvicorn app.main:app --reload --port 8000

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


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    """Run one agent turn, streaming customer-safe progress then the reply."""
    messages = _sessions.setdefault(req.session_id, [])
    messages.append({"role": "user", "content": req.message})

    progress: asyncio.Queue = asyncio.Queue()

    def emit(kind: str, payload: dict) -> None:
        bus.publish(kind, req.session_id, payload)
        if kind == "tool_call" and payload["tool"] in _PROGRESS_LABELS:
            progress.put_nowait({"kind": "working", "label": _PROGRESS_LABELS[payload["tool"]]})
        elif kind == "retry":
            progress.put_nowait({"kind": "working", "label": "Reconnecting, one moment"})

    bus.publish("customer_message", req.session_id, {"text": req.message})

    async def stream():
        task = asyncio.create_task(run_turn(messages, emit))
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
        try:
            reply = task.result()
        except Exception as exc:  # surfaced to admin stream by the loop already
            reply = (
                "I'm sorry, something went wrong on our side. "
                "Please try again in a moment."
            )
            bus.publish("error", req.session_id, {"where": "chat", "error": str(exc)})
            # Drop the failed turn so history stays consistent for a retry.
            if messages and messages[-1]["role"] == "user":
                messages.pop()
        # Flush any progress events that raced with completion.
        while not progress.empty():
            yield _sse(progress.get_nowait())
        bus.publish("agent_reply", req.session_id, {"text": reply})
        yield _sse({"kind": "reply", "text": reply})
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

    messages = _sessions.setdefault(session_id, [])
    messages.append({"role": "user", "content": transcript})
    bus.publish("customer_message", session_id, {"text": transcript, "channel": "voice"})
    try:
        reply = await run_turn(messages, emit)
    except Exception as exc:
        bus.publish("error", session_id, {"where": "voice_chat", "error": str(exc)})
        if messages and messages[-1]["role"] == "user":
            messages.pop()
        reply = (
            "I'm sorry, something went wrong on our side. "
            "Please try again in a moment."
        )
    bus.publish("agent_reply", session_id, {"text": reply, "channel": "voice"})

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

"""FastAPI entry point for the refund agent.

Run with:  uv run uvicorn app.main:app --reload --port 8000
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Refund Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# Routes to implement:
#   POST /api/chat            -> run one agent turn, stream events (SSE)
#   GET  /api/events          -> admin event stream: full agent trace (SSE)
#   GET  /api/customers       -> CRM table for the admin dashboard
#   GET  /api/policy          -> the refund policy document

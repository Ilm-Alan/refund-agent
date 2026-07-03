# Refund agent

An AI customer support agent for e-commerce refunds. Claude drives the conversation and gathers context through tools; a deterministic policy engine makes the actual approve/deny call, so the agent can't be talked past the refund policy.

- **Backend:** Python / FastAPI, raw function calling against the Anthropic Messages API as the provider contract (no agent framework). Runs on Claude or any Anthropic-compatible endpoint such as Ollama; switching providers is an .env change, not a code change. SSE event stream
- **Frontend:** React + TypeScript (Vite): customer chat and an admin dashboard with the live agent trace
- **Data:** mock CRM (15 customer profiles with orders) and a strict refund policy document

## Run it

Backend (configure a provider in `backend/.env`, see `.env.example`):

```sh
cd backend
cp .env.example .env   # pick a provider block, add your key
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

Frontend:

```sh
cd frontend
npm install
npm run dev            # http://localhost:5173
```

## Status

Scaffold. Architecture and module layout are in place; implementation notes live in each module's docstring.

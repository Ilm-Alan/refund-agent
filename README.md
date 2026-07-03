# Refund agent

An AI customer support agent for e-commerce refunds, built as a vertical slice of a fictional retailer, Waypoint Supply. The model drives the conversation and gathers context through tools; a deterministic policy engine makes the actual approve/deny call. The agent cannot be talked past the refund policy, because the policy is code, not prompt.

Two surfaces: a customer chat that takes text or voice, and an admin dashboard that shows every step the agent takes in real time (model output, tool calls, policy verdicts with rule citations, retries, errors).

## How it decides

The load-bearing design decision: the LLM never rules on a refund.

1. `app/agent/policy.py` evaluates the rules in `data/refund_policy.md` (refund window, category exclusions, frequency caps, VIP extension, fraud freeze) and returns a verdict citing the specific rule IDs it applied.
2. `check_refund_eligibility` exposes that verdict to the model as a tool.
3. `process_refund` re-runs the same check server-side before executing, and computes the refund amount itself. The model cannot pass an amount, and a model that has been sweet-talked into attempting an ineligible refund gets a refusal from the gate, visible in the admin trace as a `gate_refusal` event.

A related boundary: verdicts have two audiences. The admin event stream gets the full reasoning, including fraud-freeze escalations with their rule citation. The model only receives what it is allowed to tell the customer, so it cannot leak an account flag it never saw.

## Architecture

```
backend/
  app/
    agent/
      loop.py      manual tool-use loop, retries with backoff, step cap
      tools.py     5 tool schemas + validating dispatcher (enforces the gate)
      policy.py    deterministic policy engine, rule-cited verdicts
    events.py      asyncio pub/sub bus with bounded replay history
    store.py       in-memory CRM loaded from data/, mutated by refunds
    voice.py       speech-to-text in, text-to-speech out
    main.py        FastAPI: chat SSE, voice turns, admin SSE firehose, CRM, policy, reset
  data/            15 customer profiles + the refund policy document
  tests/           policy engine tests pinned to the CRM fixtures
frontend/
  src/views/Chat.tsx    customer chat with streaming progress states
  src/views/Admin.tsx   live trace ledger, CRM table, decision log, policy
```

The agent loop is raw function calling, written out by hand rather than through an agent framework or the SDK's tool runner. That is deliberate: every model response, tool call, tool result, and policy verdict is published to an event bus before the next API call, which is what makes the live admin trace possible. Tool schemas are strict (`additionalProperties: false`, explicit `required`), but the dispatcher does not trust schema enforcement: it re-validates every input server-side and returns descriptive error results (unknown customer, order that belongs to someone else, invalid reason code) that the model recovers from on the next step. Those recoveries show up in the trace as red tool-error rows.

Failure handling is part of the surface, not an apology: rate limits and 5xx responses retry with exponential backoff and publish `retry` events, tool validation failures come back as `is_error` tool results, and the loop caps its steps. All of it is visible in the dashboard.

## Provider

The agent targets the Anthropic Messages API as its provider contract. The client takes no credential or endpoint arguments, so the provider is chosen entirely by environment variables. Development ran against Ollama's free Anthropic-compatible endpoint; moving it to Claude, or any compatible provider, is an `.env` change, not a code change. See `backend/.env.example` for the three configurations (Ollama Cloud, Ollama local, Anthropic).

Because open-model tool calling is the weakest link in that portability story, the dispatcher assumes the model will sometimes guess IDs it has not looked up yet, call tools in the wrong order, or invent reason codes. Server-side validation turns all of those into recoverable errors instead of wrong answers.

## Voice

Voice is a thin I/O layer, not a second agent. The browser records the microphone, the backend transcribes it, and the transcript runs through the exact same loop, tools, and policy gate as a typed message; the reply comes back as text plus synthesized speech. A spoken request cannot reach any code path a typed one could not, and voice turns appear in the admin trace tagged with their channel.

The pipeline activates when `OPENAI_API_KEY` is set (see `.env.example`) and targets the OpenAI audio API shape, so any OpenAI-compatible endpoint works; `STT_MODEL`, `TTS_MODEL`, and `TTS_VOICE` are configurable. The chat shows a Speak button only when voice is configured. Transcription is treated as untrusted input like everything else: spoken emails arrive as "name at example dot com" and the model normalizes them before calling tools, with dispatcher validation as the backstop.

## Run it

Backend (Python 3.12+, [uv](https://docs.astral.sh/uv/)):

```sh
cd backend
cp .env.example .env   # pick a provider block, add your key
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

Frontend (Node 20.19+ or 22+):

```sh
cd frontend
npm install
npm run dev            # http://localhost:5173
```

Tests:

```sh
cd backend
uv run pytest
```

The customer chat is at `/`, the admin dashboard at `#/admin`. Open them in two windows side by side; the dashboard streams the trace live while you chat. "Reset demo data" restores the CRM to its on-disk state.

## Try it

The CRM is seeded so every policy rule has a customer that triggers it (day counts below are relative to the seed date, 2026-07-02). A few conversations worth having:

- `maya.chen@example.com`, defective headphones on order ORD-1024, delivered 8 days before the seed date: clean full refund. Give a wrong email or order number first and watch the agent recover from the tool error in the trace.
- `derek.vaughn@example.net`, keyboard on ORD-0937, delivered 45 days before the seed date: denied under the 30-day window (rule R1). Push back and escalate; the agent holds the line and cites the rule, and if the model ever tries to force the refund anyway, the policy gate refuses it server-side.
- `jordan.blake@example.net`: the account is frozen for fraud review. The admin trace shows the R6 escalation; the customer is told only that a specialist will follow up.
- `priya.raghavan@example.org`, jacket on ORD-0952: outside the standard window but approved through the VIP extension (R5), which itself caps out for items over 200 USD (see `tomas.rivera@example.com`).
- `rosa.delgado@example.com`, studio monitors on ORD-1061, opened, changed her mind: partial refund with the 15% restocking fee (R3), the one verdict kind the other conversations do not hit.
- `farida.haddad@example.org`, two refunds already this year: refund her scarf on ORD-1032 (that works, and puts her at the 3-per-year cap), then ask for the napkin set in the same conversation. The second request is refused under R4, because eligibility is ruled at execution time against current state, not remembered from earlier in the conversation. `tests/test_gate.py` pins this behavior.
- Any of the above, spoken: click Speak, say it, click Stop. Same loop, same gate, spoken reply.

Retries are part of the demo too: on a rate-limited provider (Ollama's free tier, for instance) the trace shows red `retry` rows with the backoff schedule before the agent recovers. Order dates in `data/customers.json` are fixed, so the in-window cases age out eventually; the policy engine takes `today` as a parameter and the tests pin it, so the suite stays green regardless.

## What production would need

Real CRM and payment integrations behind the same tool interface, persistence for sessions and the audit log, authentication separating the two views, token-level streaming for the chat, a human review queue behind the escalation path, and an eval suite that replays adversarial conversations against the gate. Identity is the other deliberate simplification: the demo trusts a claimed email, where production would establish the customer before the conversation starts (and would still refuse to fuzzy-match addresses in chat, since a did-you-mean on account emails is an enumeration leak). The policy engine and dispatcher boundaries are already shaped for that swap: tools are the only place the agent touches state.

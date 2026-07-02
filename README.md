# LiveKit Voice Interview Agent

A real-time voice agent — **"Aria"** — that conducts a spoken technical interview.
She greets the candidate, asks a planned set of questions one at a time, judges
each answer, and reacts naturally (hints, rephrasing, follow-ups) before quitting
when the interview is done.

The questions are produced by a separate **interview-planner** service and read
from MongoDB, so you can change the questions without touching or redeploying the
agent.

---

## How it works (the logic)

```
Candidate's voice ──► STT (Sarvam) ──► Agent logic ──► LLM (OpenAI) ──► TTS (Sarvam) ──► Candidate hears Aria
                                            │
                                            └── questions from MongoDB (latest set)
```

1. **Greeting** — Aria waits for the candidate to join, then greets them and
   explains the format (she always speaks first).
2. **Questions** — She fetches the **latest** question set from MongoDB and asks
   each question **in order**, exactly as written.
3. **Judging + follow-up** — After an answer, if that question's planner **score
   ≥ 0.85**, the LLM silently judges the answer and asks **one** follow-up
   question. Lower-scored questions move straight on. (Threshold:
   `FOLLOW_UP_THRESHOLD` in `agent.py`.)
4. **Quit** — When all questions are done — or the candidate asks to stop — Aria
   gives a short closing and disconnects.

### What Aria understands (intent handling)

After every answer, a small LLM call classifies the candidate's turn and Aria
reacts accordingly:

| Candidate says… | Aria does |
|---|---|
| a real answer (even "I don't know") | judges it, continues |
| "give me a hint" / "I'm stuck" | gives ONE hint without revealing the answer, re-asks |
| "rephrase / simplify that" | restates the question in simpler words |
| "repeat the question" | asks it again, verbatim |
| off-topic ("haha", "I love you", "let's get coffee") | "Let's stay focused…", re-asks |
| "I want to quit / finish" | closes warmly and disconnects |
| (questions run out) | closes warmly and disconnects |

### Where the questions come from

- **MongoDB** (primary): the agent reads the **newest document** (by `created_at`)
  from the `voice_agent_planner` collection at the start of every session. To use
  a new question set, the planner just writes a new document — **no redeploy.**
- **`questions.json`** (fallback): if the database is unreachable, the agent uses
  this bundled file so an interview never fails to start.

Expected document / file shape:

```json
{
  "interview_id": "james",
  "questions": [
    { "question": "What are the assumptions of linear regression?", "score": 0.70 },
    { "question": "What is the difference between precision and recall?", "score": 0.90 }
  ]
}
```

> Note: "latest document" is interview-agnostic — it serves whatever set was most
> recently created. This is intended for **one interview at a time**. For multiple
> concurrent candidates you'd fetch by `interview_id` instead.

---

## Tech stack

| Stage | Provider / Model |
|-------|------------------|
| Speech-to-text (STT) | Sarvam — `saaras:v3` |
| Language model (LLM) | OpenAI — `gpt-4o-mini` |
| Text-to-speech (TTS) | Sarvam — `bulbul:v3`, speaker `shubh` |
| Voice activity detection | Silero (noise-tuned) |
| Turn detection | LiveKit multilingual model |
| Question store | MongoDB (`voice_agent_planner`) |
| Real-time transport | LiveKit (Cloud or self-hosted) |

---

## Requirements

- **Python** 3.10–3.12
- **[uv](https://docs.astral.sh/uv/)** (package manager)
- **API keys / services:**
  - OpenAI API key
  - Sarvam API key (STT + TTS)
  - A MongoDB instance with a `voice_agent_planner` collection
  - A LiveKit server — **either** LiveKit Cloud **or** a self-hosted instance
    (only needed for `dev` / `start` modes, **not** for `console` mode)

### Python dependencies (installed via `uv sync`)

```
livekit-agents[openai,sarvam,silero,turn-detector]~=1.5
motor            # async MongoDB driver
openai           # for the intent-classification calls
python-dotenv
```

---

## Setup

1. **Install dependencies:**
   ```bash
   uv sync
   ```
2. **Create a `.env` file** (see variables below).
3. **Download the local models** (one-time — fetches Silero VAD + turn detector):
   ```bash
   uv run python agent.py download-files
   ```

### Environment variables (`.env`)

```bash
# OpenAI (LLM + intent detection)
OPENAI_API_KEY=

# Sarvam (STT + TTS)
SARVAM_API_KEY=

# MongoDB (question store)
DATABASE_URL=mongodb://user:pass@host:27017/dbname?authSource=admin

# LiveKit (only for `dev` / `start` modes, NOT for `console`)
LIVEKIT_URL=ws://<host>:7880        # or wss://<project>.livekit.cloud
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
```

---

## Running it

### Talk to it locally (no LiveKit needed)

`console` mode uses your computer's mic + speakers directly — the quickest way to
test the interview flow:

```bash
uv run python agent.py console
```
Press `Ctrl+C` to stop. Once Aria greets you, just start talking.
Try: *"can you give me a hint?"*, *"rephrase that more simply"*, *"let's finish here"*.

### Run against a LiveKit room (real deployment path)

Point `LIVEKIT_*` at your LiveKit server, then run the worker:

```bash
uv run python agent.py start      # production: registers as a worker
# or
uv run python agent.py dev        # development
```

The worker connects to the LiveKit server and waits. When a candidate joins a
room (via a frontend or the LiveKit Agents Playground), Aria is dispatched into
that room and the interview begins.

---

## Saving the conversation

At the end of every interview (whether it completes normally or the candidate
quits early), the full transcript is saved to the `vgi_conversation` MongoDB
collection:

```json
{
  "user_id": "127",
  "interview_id": "aria",
  "conversation": [
    { "type": "main",     "question": "<planned question>", "answer": "<candidate's answer>" },
    { "type": "followup", "question": "<generated follow-up>", "answer": "<candidate's answer>" }
  ],
  "created_at": "<UTC timestamp>"
}
```

Only genuine questions and answers are stored (not hints, repeats, or off-topic
redirects). Saving is fail-safe — a database error is logged but never crashes
the interview.

---

## Scoring & evaluation

After an interview is saved, `evaluation.py` grades it with an LLM and produces a
report matching the frontend schema (see `example.json`).

- **`evaluation.py`** — fetches the latest `vgi_conversation` for a `user_id`,
  asks the LLM to score it (easy-to-medium, intent-based judging), and assembles
  the report. It computes `overall`, `dimensions`, `questionReviews`,
  `sessionType`, and `completedAt`; all other keys are frontend-managed
  placeholders.
- **`api.py`** — a small FastAPI server exposing the report over HTTP.

Scoring model:

| Part | Scale | Notes |
|------|-------|-------|
| `dimensions` | `technical` 0–40, `depth` 0–25, `communication` 0–20, `problemSolving` 0–15 | LLM gives a percent per dimension; points are derived |
| `overall.score` | 0–100 | sum of the four dimension points |
| `questionReviews[].score` | 0–40 | per-question, with `good` / `improve` feedback |

### Run the evaluation

CLI (prints the JSON report):

```bash
uv run python evaluation.py <user_id>     # e.g. 127
```

HTTP API:

```bash
uv run uvicorn api:app --reload --port 8001
```

| Method | Path | Returns |
|--------|------|---------|
| GET | `/health` | `{"status":"ok"}` |
| GET | `/evaluation/{user_id}` | the scored report for that user's latest interview |
| GET | `/docs` | interactive Swagger UI |

> Each call runs a live LLM evaluation (~3–6s) and is not cached.

---

## Architecture note (where to run the agent)

The **agent worker** (this code) and the **LiveKit server** are two separate
things:

- **LiveKit server** = the media relay that connects browsers and the agent.
- **Agent worker** = this `agent.py`, the "brain" doing STT/LLM/TTS.

The worker can run anywhere with network access to the LiveKit server. For good
audio quality and low latency, run the worker **close to** (ideally on / beside)
the LiveKit server rather than far away — audio quality degrades when the brain
is geographically distant from the media server.

- **LiveKit Cloud:** the worker can be hosted by LiveKit via the `lk agent deploy`
  CLI (managed hosting).
- **Self-hosted LiveKit:** run the worker yourself (e.g. `uv run python agent.py
  start`, or the included `Dockerfile`) on a machine near the server.

---

## Files

| File | Purpose |
|------|---------|
| `agent.py` | The interview agent (all logic + conversation saving). |
| `evaluation.py` | Scores a saved conversation with an LLM (produces the report). |
| `api.py` | FastAPI server exposing the evaluation report over HTTP. |
| `questions.json` | Fallback question set if MongoDB is unreachable. |
| `example.json` | Target schema for the evaluation report (from the frontend). |
| `pyproject.toml` / `uv.lock` | Dependencies. |
| `Dockerfile` | Container image for deploying the worker. |
| `.env` | Secrets / config (not committed). |

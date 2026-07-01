# Autonomous Document Agent

An autonomous AI agent that takes a natural-language request, **plans its own
task list**, **executes each step**, **self-checks its work**, and produces a
**polished Microsoft Word (`.docx`)** business document — proposal, business
report, project plan, meeting minutes, technical design, SOP or product spec.

Exposed as a **FastAPI** service: `POST /agent` with `{"request": "..."}`.

> **Runs with zero API keys.** A deterministic offline engine guarantees the
> full pipeline (and the demo) works without credentials. Plug in a free LLM
> (Groq / Gemini / Ollama) via `.env` and the *same* code path produces richer,
> more tailored documents.

---

## 1. Quick start

```bash
cd autonomous-doc-agent
pip install -r requirements.txt

# Option A — run the two required demo scenarios (offline, no keys needed)
python -m examples.run_examples

# Option B — run the API
python -m uvicorn app.main:app --reload
#   -> open http://127.0.0.1:8000/docs  (interactive Swagger UI)

# Run the tests
python -m pytest -q
```

### Call the API

```bash
curl -X POST http://127.0.0.1:8000/agent \
  -H "Content-Type: application/json" \
  -d '{"request": "Write a business proposal for an AI customer support chatbot."}'
```

The JSON response contains the agent's **plan (task list)**, the **self-check
result**, an execution **trace**, and a **`document.download_url`**. Fetch it:

```bash
curl -OJ http://127.0.0.1:8000/documents/<id>     # downloads the .docx
```

(Or pass `"include_base64": true` to get the document inline in the response.)

### Use a real LLM (optional)

```bash
cp .env.example .env
# then set ONE of:
#   GROQ_API_KEY=...      (free: https://console.groq.com)
#   GEMINI_API_KEY=...    (free: https://aistudio.google.com/apikey)
#   USE_OLLAMA=1          (local: `ollama serve`, `ollama pull llama3.1`)
```

`GET /health` reports which providers are active.

---

## 2. Architecture

```
                                  POST /agent {"request": "..."}
                                              │
                                              ▼
                              ┌──────────────────────────────┐
                              │       AgentOrchestrator       │  control loop
                              │  validate → plan → execute →  │  + guardrails
                              │  reflect → revise → render    │  + trace/tasks
                              └───────────────┬──────────────┘
        ┌───────────────┬───────────────┬─────┴────────┬───────────────┐
        ▼               ▼               ▼              ▼               ▼
    Planner         Executor        Reflector     Doc Builder      LLMClient
 (what to do)   (draft sections)  (self-check)   (python-docx)   (retry + fallback)
        │               │               │                            │
        │               │  uses Tools   │                     ┌──────┴───────┐
        │               └──►(dates,     │                     ▼      ▼       ▼
        │                    mock data, │                   Groq  Gemini  Ollama
        └────────── shared templates ───┘                    └── heuristic ──┘
                     knowledge base                             fallback
```

Every stage tries the LLM first and degrades gracefully to a deterministic
implementation, so the agent is **never blocked** by a missing/failing model.

### Agent workflow & planning logic

1. **Validate / guardrails** — length bounds + a content-policy check (→ HTTP 422).
2. **Plan** — classify the document type, choose an audience/tone, design the
   sections, record **assumptions** for anything ambiguous, and derive a concrete
   **TODO list** where every task maps to real downstream work.
3. **Execute** — draft each section independently (prose + bullets + tables),
   marking its task `done` as it goes.
4. **Reflect (self-check)** — critique the draft against the plan.
5. **Revise** — a bounded (max 1) revision pass re-drafts only the weak sections.
6. **Render** — assemble a styled `.docx` (title page, contents, numbered
   sections, shaded tables, appendix, page-numbered footer).

### LLM integration

`LLMClient` holds an **ordered provider chain** (Groq → Gemini → Ollama), each
called through a tiny `complete()` interface over raw HTTP (`httpx`) — no vendor
SDKs, so the dependency surface stays small. It adds **per-provider retries with
exponential backoff** and a **JSON-repair** parser (`try_parse_json`) that
survives code fences and surrounding prose. If every provider fails (or none is
configured), each call falls back to a **deterministic heuristic** and reports
`heuristic-fallback` in the trace.

### Tool orchestration

The executor orchestrates small, pure tools via a `ToolRegistry`:
`today` (date), `classify_heading` (routing), and `mock_table` (synthesises
realistic, seeded timelines / budgets / risk registers / RACI / metrics). New
tools (e.g. a real data source) can be registered without touching the executor.

### Document generation

`python-docx` with a branded theme: navy/blue palette, a title page + metadata
table, a contents list, numbered `Heading 1/2` sections, **shaded-header,
zebra-striped tables** (implemented via raw OOXML `w:shd`, which python-docx has
no high-level API for), an **Appendix** listing the agent's assumptions and its
self-check result, and a footer with a live `PAGE` field.

---

## 3. The mandatory engineering improvement — **Reflection / Self-check**

**What.** After drafting, a dedicated `Reflector` stage critiques the document
*before* it is returned. It runs two layers:

- **Deterministic structural checks (always on):** every planned section present,
  no threadbare sections (`< 25` words), no empty tables, no placeholder text
  (`TBD`, `lorem ipsum`, `[TODO]`…), and a whole-document length floor. It emits a
  `quality_score`, an `issues` list, and the exact `sections_to_improve`.
- **LLM qualitative critique (when a provider is configured):** judges clarity,
  relevance and tone, then is **merged** with the structural verdict (union of
  concerns, minimum score).

If the check fails, a **bounded revision pass** re-drafts *only* the flagged
sections (feeding the critique back into the executor) and re-checks once.

**Why I chose it.** The biggest risk in a generate-and-ship agent is confidently
returning incomplete or generic output. Reflection is the single highest-leverage
improvement for **output quality and trust**, and it's the most "agentic" — the
system reasons about its *own* work rather than just producing it.

**How it improves the agent.** It turns a one-shot generator into a
**closed-loop** one: measurable quality (`quality_score`), automatic recovery
from thin sections, and full transparency — the reflection result and every step
are surfaced in the API response *and* written into the document's appendix.

> The codebase also demonstrates **retry & fallback logic** (provider chain +
> heuristic degradation) and **request validation & guardrails**, but reflection
> is the headline improvement.

---

## 4. The two required test inputs

Both are wired into `examples/run_examples.py`.

**① Standard business request**
> *"Write a business proposal for an AI-powered customer support chatbot for a
> mid-sized e-commerce company."*

→ Agent picks **proposal**, plans 13 tasks / 9 sections, and produces a proposal
with a timeline, pricing and risk tables.

**② Complex / ambiguous request** (missing format, unclear timeline, conflicting
goals — the agent must decide its own plan)
> *"We have a leadership offsite coming up and need a document about speeding up
> our product delivery without sacrificing quality or burning out the team.
> Budget is tight, timeline is unclear, and leadership hasn't agreed on the
> format — decide the best structure yourself and make reasonable assumptions."*

→ Agent resolves the ambiguity: chooses **business report**, records explicit
**assumptions**, and produces a complete, self-checked document.

---

## 5. API reference

| Method | Path | Purpose |
| ------ | ---- | ------- |
| `POST` | `/agent` | Run the agent. Body: `{"request": "...", "include_base64": false}` |
| `GET`  | `/documents/{id}` | Download a generated `.docx` |
| `GET`  | `/health` | Liveness + active LLM providers |
| `GET`  | `/` → `/docs` | Interactive Swagger UI |

`POST /agent` returns: `document_type`, `title`, `summary`, the full `plan`
(tasks + assumptions + sections), the `reflection` result, `document`
(id / download_url / size / word_count), `llm_provider`, `elapsed_seconds`, and a
step-by-step `trace`.

---

## 6. Project structure

```
autonomous-doc-agent/
├── app/
│   ├── main.py                 # FastAPI app & endpoints
│   ├── config.py               # env-driven settings
│   ├── schemas.py              # Pydantic models (pipeline + API contract)
│   ├── llm/
│   │   ├── base.py             # provider interface + Groq/Gemini/Ollama
│   │   └── client.py           # retry + fallback + JSON repair
│   ├── agent/
│   │   ├── orchestrator.py     # the autonomous control loop
│   │   ├── planner.py          # request -> plan + TODO list
│   │   ├── executor.py         # draft & revise sections
│   │   ├── reflector.py        # self-check (the engineering improvement)
│   │   ├── templates.py        # document-type knowledge base
│   │   └── tools.py            # tool registry + mock-data generators
│   └── document/
│       └── builder.py          # polished .docx rendering
├── examples/run_examples.py    # the two demo scenarios
├── tests/test_agent.py         # 10 offline end-to-end + unit tests
├── requirements.txt
└── .env.example
```

---

## 7. Notes for the video walkthrough

**Debugging insight.** Styled tables were the tricky part: `python-docx` has *no*
high-level API for cell background colour, so header shading silently did
nothing. Root cause — cell fill lives in the OOXML `<w:tcPr><w:shd>` element that
the library doesn't expose. Fix — build the `w:shd` element by hand
(`OxmlElement("w:shd")` with `w:fill`) and append it to each cell's `tcPr`
(`app/document/builder.py::_shade_cell`). The same low-level technique drives the
page-number field in the footer. A second, subtler bug was LLMs wrapping JSON in
```` ```json ```` fences or prose — handled by the tolerant `try_parse_json`
extractor plus schema-repair in the planner/executor.

**Tradeoff — Autonomous planning vs. deterministic workflows.** A fully
LLM-driven planner is maximally flexible but non-deterministic, slower, and can
fail. I chose a **hybrid**: the LLM proposes the plan/content, but a deterministic
template knowledge base and validation layer *constrain and repair* it (and take
over entirely offline). This buys reliability, testability and zero-cost demos at
the price of some ceiling on creativity — a deliberate trade for a system that
must always return a usable document.

**Scalability & architecture thinking.** Stateless request handling; sections are
drafted independently so the executor can be parallelised with `asyncio`; the
provider chain and tool registry are open for extension; documents persist to
disk and are indexed for retrieval; guardrails and bounded revision keep latency
and cost predictable. Natural next steps: async section fan-out, a real vector
store for RAG-grounded content, and swapping the in-memory index for object
storage.

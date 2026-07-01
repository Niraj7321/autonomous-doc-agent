# Autonomous Document Agent — Presentation Script

**Total: ~8–10 min.** Timings per section below. Commands are copy-paste (PowerShell / Windows).

---

## 0. One-time setup before you present (do this off-camera)

```powershell
cd C:\Users\Niraj\autonomous-doc-agent
python -m pip install -r requirements.txt        # if not already
python -m pytest -q                              # confirm green: everything passes offline
```
Have two terminals ready, plus a browser tab on **http://127.0.0.1:8000/docs**.

> Talking-point safety net: the whole system runs with **zero API keys** via a deterministic
> offline engine, so the demo can never fail on a missing key or rate limit.

---

## 1. Live Demo (3–4 min)

**Goal:** show it end-to-end on **both** test cases, including the agent-generated task list
and the final Word document.

### The two test cases (already coded in `examples/run_examples.py`)
1. **Standard request** — *"Write a business proposal for an AI-powered customer support
   chatbot for a mid-sized e-commerce company."* → agent classifies it as a **proposal**.
2. **Complex / ambiguous request** — *"...leadership offsite ... speed up product delivery
   without sacrificing quality ... budget tight, timeline unclear, format not agreed — decide
   the structure yourself and make reasonable assumptions."* → agent **picks the document type
   itself** and **records its assumptions**.

### Run it (this single command IS the demo)
```powershell
python -m examples.run_examples
```
As it runs, narrate what's on screen for each case:
- **"DECIDED DOCUMENT TYPE / TITLE"** — the agent *chose* this; I didn't tell it.
- **"AGENT-GENERATED TASK LIST (TODO)"** — point at the `[x]` items: *"it wrote its own to-do
  list from the sections it planned, then checked each one off as it completed it."*
- **"ASSUMPTIONS MADE"** (case 2) — *"the request was ambiguous, so instead of asking questions
  it made explicit, reasonable assumptions and recorded them in the document."*
- **"SELF-CHECK (reflection)"** — *"it graded its own draft 0–100 before finalising."*
- **"EXECUTION TRACE"** — *"every stage is logged with which engine produced it — full
  observability."*

### Show the final Word document
```powershell
Invoke-Item .\generated
```
Open one of the two generated `.docx` files and scroll: **title page → contents → numbered
sections with tables → Appendix A** (which prints the assumptions and the self-check result).

> Optional API angle (30s): open **/docs**, `POST /agent`, *Try it out*, Execute, then open the
> returned `download_url`. Same pipeline, exposed over HTTP.

---

## 2. What You Built (2–3 min)

> **One-liner:** *"A single autonomous agent, exposed as a FastAPI service, that turns one
> English sentence into a polished Word document by planning its own tasks, drafting each
> section, critiquing its own work, and rendering a styled `.docx` — with a deterministic
> fallback so it works with no LLM at all."*

**Architecture — the agent loop** (`app/agent/orchestrator.py`):
```
validate → plan → execute → reflect → (revise ≤1) → render → respond
```

Walk the layers (name the file for each):

- **API design** (`app/main.py`, FastAPI): three endpoints — `POST /agent`, `GET
  /documents/{id}`, `GET /health` — auto-documented Swagger UI, Pydantic request/response
  models, generated docs persist to disk and are indexed by id.
- **Planning logic** (`app/agent/planner.py` + `templates.py`): asks the LLM for a structured
  plan (document_type, title, audience, tone, **sections**, assumptions). The **TODO list is
  *derived* from the chosen sections** (`_build_tasks`) so every task maps to real work. If no
  LLM (or bad output), a **document-type knowledge base** (7 types: proposal, report, project
  plan, meeting minutes, technical design, SOP, product spec) provides a solid outline.
- **LLM integration** (`app/llm/`): an **ordered provider chain — Groq → Gemini → Ollama →
  heuristic-fallback** — with per-provider retries + exponential backoff. Calls raw HTTP via
  `httpx` (no vendor SDKs) to keep dependencies small. `generate_json` guarantees a usable dict:
  parse the model output, and if nothing parses, run the deterministic fallback.
- **Tool usage** (`app/agent/tools.py`): a small **ToolRegistry** of pure tools —
  `today`, `classify_heading`, and `mock_table` (deterministic, subject-seeded mock data for
  timelines, budgets, risks, metrics, roles, API tables…). Explicit and traceable, and trivial
  to extend with a real data source later.
- **Execution** (`app/agent/executor.py`): drafts each section into typed content **blocks**
  (paragraph / subheading / bullets / table). Same machinery powers the revision pass.
- **Self-check / reflection** (`app/agent/reflector.py`): deterministic structural checks
  (missing sections, thin content < 25 words, empty tables, placeholder text, doc < 250 words)
  **plus** an optional LLM critique, merged (min score, union of issues). Drives a **bounded
  1-pass revision**.
- **Document generation** (`app/document/builder.py`, `python-docx`): branded title page,
  metadata table, contents, numbered sections with **shaded/zebra tables**, footer with page
  numbers, and an **Appendix** recording assumptions + the self-check. Returns raw bytes.
- **Schemas** (`app/schemas.py`, Pydantic): single source of truth. `Block` is a deliberately
  **flat tagged union** — one `type` field + optional fields — which makes the JSON an LLM must
  emit far simpler to validate and repair.

**Technologies:** Python 3, FastAPI + Uvicorn, Pydantic v2, python-docx, httpx, pytest;
optional Groq / Gemini / Ollama.

---

## 3. Debugging Insight (1–2 min) — *pick ONE*

### Option A (recommended) — LLMs don't return clean JSON
- **Issue:** with a real LLM enabled, the plan/section stages intermittently failed to parse.
- **Root cause:** models don't honour "return only JSON" — they wrap it in ```` ```json ```` code
  fences, add prose like *"Sure! Here it is:"*, or emit trailing commas. `json.loads` throws on
  all three.
- **Fix:** a resilient `try_parse_json` (`app/llm/client.py`) that strips code fences, then
  slices to the **outermost `{...}` / `[...]` span** and retries. Layered defence around it:
  request `json_mode` when the provider supports it, coerce/validate each block, and if parsing
  still fails, fall through to the deterministic engine so the request never dies. It's pinned
  by a unit test (`test_try_parse_json_handles_fences_and_prose`).

### Option B (simple, and you saw it live) — Windows console mojibake
- **Issue:** the demo output (and the recorded video) showed `�` instead of em-dashes and smart
  quotes in titles/section text.
- **Root cause:** Windows consoles default to the legacy **cp1252** code page, so UTF-8 glyphs
  the agent produced were mis-rendered on stdout.
- **Fix:** `sys.stdout.reconfigure(encoding="utf-8")` at the top of the demo runner
  (`examples/run_examples.py`), guarded in a try/except so it never breaks the demo. Clean glyphs
  everywhere afterward.

### Option C — the LLM under-produces sections
- **Issue:** occasionally the LLM returned only 1–2 sections, yielding a threadbare document.
- **Root cause:** models sometimes ignore the "6–11 sections" instruction.
- **Fix:** the planner detects `len(sections) < 3` and repairs from the template knowledge base,
  labelling the provider `"<provider>+template-repair"` so the trace stays honest.

---

## 4. Tradeoff Discussion (1–2 min) — *pick ONE*

### Option A (recommended) — Autonomous Planning vs Deterministic Workflows
- **The tension:** an LLM planning freely is flexible and tailored but non-deterministic — it can
  hallucinate, drift, return malformed output, or be unavailable. A hard-coded template workflow
  is reliable and fast but rigid and generic.
- **My choice — a hybrid, not a pick-one:** the LLM plans/drafts/critiques when available, but a
  **deterministic engine backs every stage** and takes over on failure or absence. The self-check
  + bounded revision reins in LLM variance; the template knowledge base guarantees a floor of
  quality. *Result:* the system is as good as the LLM when there is one, and still fully
  functional (and demoable) when there isn't.
- **The cost I accepted:** more code and two code paths to maintain, and the offline output is
  more generic than a top-tier LLM's. I judged **guaranteed reliability + a zero-dependency demo**
  worth that extra surface area.

### Option B — Single-agent vs Multi-agent
- **The tension:** I could have split planner/writer/critic into separate autonomous agents that
  message each other. That's more "agentic" and parallelisable, but adds orchestration
  complexity, latency, cost, and failure modes.
- **My choice:** **one agent with distinct internal stages** (plan → execute → reflect → revise).
  It captures the same plan/draft/critique loop with far less coordination overhead and a single,
  linear, fully-observable trace. Multi-agent is a clean future extension (the tool registry and
  stage separation already make it easy), but it wasn't justified for single-document generation.

### Option C — Speed / Simplicity vs Functionality
- Raw `httpx` calls instead of vendor SDKs, a flat block schema, and one bounded revision pass —
  each trades a bit of theoretical capability for a smaller dependency surface, simpler LLM JSON,
  and predictable latency/cost.

---

## Quick-reference cheat sheet (keep visible while presenting)

| Ask | Answer |
|-----|--------|
| Endpoints | `POST /agent`, `GET /documents/{id}`, `GET /health` (+ Swagger `/docs`) |
| Pipeline | validate → plan → execute → reflect → revise(≤1) → render → respond |
| Doc types | proposal, business report, project plan, meeting minutes, technical design, SOP, product spec |
| LLM chain | Groq → Gemini → Ollama → heuristic-fallback (retries + backoff) |
| Tools | `today`, `classify_heading`, `mock_table` (seeded, deterministic) |
| Self-check | structural rules + optional LLM critique, score 0–100, 1 bounded revision |
| Output | styled `.docx`: title page, contents, tables, appendix (assumptions + self-check) |
| Runs offline? | Yes — zero keys; deterministic engine guarantees the pipeline |
| Tests | `python -m pytest -q` — both required scenarios + API + validation, all offline |
```

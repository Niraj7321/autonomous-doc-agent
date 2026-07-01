# Autonomous Document Agent — Flow Diagram

## End-to-end pipeline (`POST /agent`)

```mermaid
flowchart TD
    A([Client: POST /agent<br/>request: plain-English sentence]) --> B{0 · VALIDATE<br/>guardrails}
    B -- empty / too short / too long / unsafe --> BX[/HTTP 422<br/>AgentValidationError/]
    B -- ok --> C[1 · PLAN<br/>Planner<br/>type · title · audience · tone<br/>sections + self-generated TODO list]
    C --> D[2 · EXECUTE<br/>Executor drafts every section<br/>paragraphs · bullets · tables]
    D --> E[3 · REFLECT<br/>Reflector self-check<br/>score 0–100 · issues · weak sections]
    E --> F{passed?<br/>or revisions used?}
    F -- no · weak sections · revisions < 1 --> G[3b · REVISE<br/>rewrite weak sections] --> E
    F -- yes / budget spent --> H[4 · RENDER<br/>build_docx → styled .docx]
    H --> I[5 · RESPOND<br/>save file · index by id<br/>return download_url + plan<br/>+ reflection + trace]
    I --> J([Client downloads<br/>GET /documents/&#123;id&#125;])

    classDef stage fill:#2b6cb2,stroke:#1f2a44,color:#fff;
    classDef gate fill:#f6ad55,stroke:#7b341e,color:#1a202c;
    classDef err fill:#e53e3e,stroke:#742a2a,color:#fff;
    classDef io fill:#edf2f7,stroke:#2d3748,color:#1a202c;
    class C,D,E,G,H,I stage;
    class B,F gate;
    class BX err;
    class A,J io;
```

## How each stage gets its content — LLM provider fallback chain

Every generative stage (PLAN / EXECUTE / REFLECT) calls `LLMClient`, which walks
providers in priority order and always degrades to a deterministic offline engine.

```mermaid
flowchart LR
    S[Stage needs content<br/>plan / draft / review] --> G1{Groq<br/>configured?}
    G1 -- yes, ok --> OUT[(valid JSON<br/>+ provider name)]
    G1 -- fail/retry --> G2{Gemini<br/>configured?}
    G2 -- yes, ok --> OUT
    G2 -- fail/retry --> G3{Ollama<br/>running?}
    G3 -- yes, ok --> OUT
    G3 -- none available --> HF[heuristic-fallback<br/>deterministic · offline · zero keys]
    HF --> OUT

    classDef prov fill:#2b6cb2,stroke:#1f2a44,color:#fff;
    classDef fb fill:#38a169,stroke:#1c4532,color:#fff;
    classDef out fill:#edf2f7,stroke:#2d3748,color:#1a202c;
    class G1,G2,G3 prov;
    class HF fb;
    class S,OUT out;
```

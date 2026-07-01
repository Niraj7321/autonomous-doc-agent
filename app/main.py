"""FastAPI service exposing the autonomous document agent.

Endpoints
---------
POST /agent            Run the agent on a natural-language request.
GET  /documents/{id}   Download a previously generated .docx.
GET  /health           Liveness + configured LLM providers.
GET  /                 Redirects to interactive API docs.
"""
from __future__ import annotations

import base64
import uuid
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from .agent import AgentOrchestrator, AgentValidationError
from .config import settings
from .schemas import AgentRequest, AgentResponse, DocumentInfo

app = FastAPI(
    title="Autonomous Document Agent",
    version="1.0.0",
    description=(
        "An autonomous agent that plans, drafts, self-checks and renders a "
        "polished Microsoft Word document from a natural-language request."
    ),
)

# One orchestrator (holds the provider chain). Stateless per request.
orchestrator = AgentOrchestrator(settings)

# Simple document store: id -> filesystem path. Files also persist on disk so
# they survive restarts; the dict is a fast in-memory index.
_OUTPUT_DIR = Path(settings.output_dir)
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
_DOCUMENT_INDEX: Dict[str, Path] = {}


def _safe_filename(title: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_" else "" for c in title)
    slug = "_".join(keep.split())[:60] or "document"
    return f"{slug}.docx"


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "llm_providers": orchestrator.llm.provider_names or ["heuristic-fallback"],
        "llm_active": orchestrator.llm.has_llm,
    }


@app.post("/agent", response_model=AgentResponse)
def run_agent(payload: AgentRequest, request: Request) -> AgentResponse:
    try:
        result = orchestrator.run(payload.request)
    except AgentValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - never leak a raw stack trace
        raise HTTPException(status_code=500, detail=f"Agent failed: {exc}") from exc

    # Persist the document and index it.
    doc_id = uuid.uuid4().hex[:12]
    filename = _safe_filename(result.plan.title)
    path = _OUTPUT_DIR / f"{doc_id}_{filename}"
    path.write_bytes(result.docx_bytes)
    _DOCUMENT_INDEX[doc_id] = path

    download_url = str(request.base_url).rstrip("/") + f"/documents/{doc_id}"
    document = DocumentInfo(
        id=doc_id,
        filename=filename,
        download_url=download_url,
        size_bytes=len(result.docx_bytes),
        word_count=result.word_count,
        base64=base64.b64encode(result.docx_bytes).decode() if payload.include_base64 else None,
    )

    return AgentResponse(
        request=payload.request,
        document_type=result.plan.document_type,
        title=result.plan.title,
        summary=result.summary,
        plan=result.plan,
        reflection=result.reflection,
        document=document,
        llm_provider=result.llm_provider,
        elapsed_seconds=result.elapsed,
        trace=result.trace,
    )


@app.get("/documents/{doc_id}")
def download_document(doc_id: str) -> FileResponse:
    path = _DOCUMENT_INDEX.get(doc_id)
    if path is None:  # fall back to scanning disk (survives restarts)
        matches = list(_OUTPUT_DIR.glob(f"{doc_id}_*.docx"))
        path = matches[0] if matches else None
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="Document not found.")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=path.name.split("_", 1)[-1],
    )

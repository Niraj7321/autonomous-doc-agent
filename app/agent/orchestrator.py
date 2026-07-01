"""AgentOrchestrator: the autonomous control loop tying the stages together.

Flow:  validate -> plan (self-generated TODO list) -> execute each task ->
self-check (reflection) -> bounded revision -> render .docx.

The orchestrator owns request validation/guardrails, task-status bookkeeping and
a structured trace so the whole run is observable end-to-end.
"""
from __future__ import annotations

import time
from collections import Counter
from typing import List, Tuple

from ..config import Settings, settings as default_settings
from ..document.builder import build_docx
from ..llm import LLMClient
from ..schemas import (
    DocumentPlan,
    ReflectionResult,
    SectionContent,
    TraceStep,
)
from . import templates
from .executor import Executor
from .planner import Planner
from .reflector import Reflector

# revision passes allowed after self-check (keeps latency/cost bounded)
_MAX_REVISIONS = 1

_UNSAFE_MARKERS = (
    "how to make a bomb", "build a bomb", "child sexual", "cp video",
    "credit card dump", "how to hack into", "ransomware source",
)


class AgentValidationError(ValueError):
    """Raised when a request fails guardrails; surfaced as HTTP 422."""


class AgentResult:
    """Container for everything the API layer needs to build a response."""

    def __init__(
        self,
        plan: DocumentPlan,
        contents: List[SectionContent],
        reflection: ReflectionResult,
        docx_bytes: bytes,
        summary: str,
        llm_provider: str,
        trace: List[TraceStep],
        elapsed: float,
    ):
        self.plan = plan
        self.contents = contents
        self.reflection = reflection
        self.docx_bytes = docx_bytes
        self.summary = summary
        self.llm_provider = llm_provider
        self.trace = trace
        self.elapsed = elapsed

    @property
    def word_count(self) -> int:
        return sum(c.word_count() for c in self.contents)


class AgentOrchestrator:
    def __init__(self, settings: Settings = default_settings, llm: LLMClient | None = None):
        self.settings = settings
        self.llm = llm or LLMClient(settings)
        self.planner = Planner(self.llm)
        self.executor = Executor(self.llm)
        self.reflector = Reflector(self.llm)

    # ------------------------------------------------------------------ #
    # Guardrails
    # ------------------------------------------------------------------ #
    def validate(self, request: str) -> str:
        if request is None or not request.strip():
            raise AgentValidationError("Request must not be empty.")
        cleaned = request.strip()
        if len(cleaned) < self.settings.min_request_chars:
            raise AgentValidationError(
                f"Request is too short (min {self.settings.min_request_chars} characters)."
            )
        if len(cleaned) > self.settings.max_request_chars:
            raise AgentValidationError(
                f"Request is too long (max {self.settings.max_request_chars} characters)."
            )
        low = cleaned.lower()
        if any(marker in low for marker in _UNSAFE_MARKERS):
            raise AgentValidationError("Request rejected by content policy.")
        return cleaned

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #
    def run(self, request: str) -> AgentResult:
        start = time.perf_counter()
        trace: List[TraceStep] = []
        providers_used: List[str] = []

        cleaned = self.validate(request)
        trace.append(TraceStep(step="validate", status="ok", detail="Request passed guardrails."))

        # 1. PLAN
        plan, plan_provider = self.planner.plan(cleaned)
        providers_used.append(plan_provider)
        _set_status(plan, [1, 2], "done")
        trace.append(TraceStep(
            step="plan", status="ok", provider=plan_provider,
            detail=f"{templates.DOC_TYPE_LABELS.get(plan.document_type, plan.document_type)} "
                   f"with {len(plan.sections)} sections; {len(plan.tasks)} tasks planned.",
        ))

        # 2. EXECUTE (draft every section)
        contents, exec_providers = self.executor.execute(plan)
        providers_used.extend(exec_providers)
        _mark_section_tasks_done(plan)
        trace.append(TraceStep(
            step="execute", status="ok",
            provider=_dominant(exec_providers),
            detail=f"Drafted {len(contents)} sections "
                   f"({sum(c.word_count() for c in contents)} words).",
        ))

        # 3. REFLECT + bounded revision
        reflection, refl_provider = self.reflector.review(plan, contents)
        providers_used.append(refl_provider)
        trace.append(TraceStep(
            step="reflect", status="ok", provider=refl_provider,
            detail=f"Quality {reflection.quality_score}/100; "
                   f"{len(reflection.issues)} issue(s); "
                   f"passed={reflection.passed}.",
        ))

        revisions = 0
        while (not reflection.passed) and reflection.sections_to_improve and revisions < _MAX_REVISIONS:
            revisions += 1
            contents = self.executor.revise(plan, contents, reflection)
            reflection_after, refl_provider2 = self.reflector.review(plan, contents)
            reflection_after.revised = True
            providers_used.append(refl_provider2)
            trace.append(TraceStep(
                step=f"revise#{revisions}", status="ok", provider=refl_provider2,
                detail=f"Revised {len(reflection.sections_to_improve)} section(s); "
                       f"quality {reflection.quality_score} -> {reflection_after.quality_score}.",
            ))
            reflection = reflection_after
        reflection.revised = revisions > 0
        _find_task(plan, "Self-review").status = "done"

        # 4. RENDER .docx
        docx_bytes = build_docx(plan, contents, reflection)
        _find_task(plan, "Render").status = "done"
        trace.append(TraceStep(
            step="render", status="ok",
            detail=f"Generated .docx ({len(docx_bytes)} bytes).",
        ))

        summary = self._summarise(plan, contents, reflection)
        elapsed = round(time.perf_counter() - start, 3)
        headline_provider = plan_provider if plan_provider != "heuristic-fallback" else _dominant(providers_used)

        return AgentResult(
            plan=plan,
            contents=contents,
            reflection=reflection,
            docx_bytes=docx_bytes,
            summary=summary,
            llm_provider=headline_provider,
            trace=trace,
            elapsed=elapsed,
        )

    # ------------------------------------------------------------------ #
    def _summarise(self, plan: DocumentPlan, contents: List[SectionContent], reflection: ReflectionResult) -> str:
        label = templates.DOC_TYPE_LABELS.get(plan.document_type, "document")
        words = sum(c.word_count() for c in contents)
        headings = ", ".join(c.heading for c in contents[:4])
        more = "…" if len(contents) > 4 else ""
        revised = " The draft was refined after a self-check." if reflection.revised else ""
        return (
            f"I interpreted your request as a {label.lower()} and autonomously planned "
            f"{len(plan.tasks)} tasks. I drafted {len(contents)} sections "
            f"({headings}{more}) totalling ~{words} words, then ran a self-check "
            f"(quality {reflection.quality_score}/100).{revised} "
            f"The polished Word document is ready to download."
        )


# --------------------------------------------------------------------------- #
# small task-list helpers
# --------------------------------------------------------------------------- #
def _set_status(plan: DocumentPlan, ids: List[int], status: str) -> None:
    for t in plan.tasks:
        if t.id in ids:
            t.status = status  # type: ignore[assignment]


def _mark_section_tasks_done(plan: DocumentPlan) -> None:
    for t in plan.tasks:
        if t.title.startswith("Draft section:"):
            t.status = "done"  # type: ignore[assignment]


def _find_task(plan: DocumentPlan, needle: str):
    for t in plan.tasks:
        if needle.lower() in t.title.lower():
            return t
    # never fail the run over bookkeeping
    return plan.tasks[-1]


def _dominant(providers: List[str]) -> str:
    real = [p for p in providers if p and p != "heuristic-fallback"]
    pool = real or providers
    if not pool:
        return "heuristic-fallback"
    return Counter(pool).most_common(1)[0][0]

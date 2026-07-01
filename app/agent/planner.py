"""Planning stage: turn a natural-language request into a DocumentPlan + TODO list.

The planner is the agent's "decide what to do" step. It first tries an LLM to
produce a tailored outline; if that is unavailable or malformed it falls back to
a deterministic, template-driven plan so the agent is never blocked. Either way,
the concrete TODO list is derived from the chosen sections so every planned task
maps to real downstream work.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ..llm import LLMClient
from ..schemas import DocumentPlan, SectionSpec, Task
from . import templates
from .tools import tools

_ALLOWED_TYPES = list(templates.DOC_TYPE_LABELS.keys())

_PLANNER_SYSTEM = (
    "You are an expert business analyst and document architect. You decompose a "
    "user's request into a precise plan for a single, polished business document. "
    "You always respond with a single valid JSON object and nothing else."
)

_PLANNER_USER_TMPL = """\
Create an execution plan for the following request.

REQUEST:
\"\"\"{request}\"\"\"

Choose the most appropriate document_type from this list:
{types}

Return JSON with EXACTLY these keys:
{{
  "document_type": "<one of the list above>",
  "title": "<concise, professional document title>",
  "subtitle": "<one-line subtitle>",
  "audience": "<intended readers>",
  "tone": "<e.g. professional, persuasive, technical>",
  "assumptions": ["<assumptions you made to resolve missing/ambiguous info>"],
  "sections": [
    {{
      "heading": "<section heading>",
      "purpose": "<what this section achieves>",
      "content_types": ["paragraph" | "bullets" | "table" | "subheading"],
      "key_points": ["<bullet-level points to cover>"]
    }}
  ]
}}

Rules:
- Produce 6-11 sections appropriate to the document_type.
- Use "table" content_type for schedules, budgets, risks, action items, metrics, roles.
- If the request is ambiguous or missing details, make reasonable assumptions and
  record them in "assumptions" (do not ask questions).
"""


def _heuristic_plan_dict(request: str) -> Dict[str, Any]:
    doc_type = templates.detect_document_type(request)
    subject = templates.derive_subject(request)
    label = templates.DOC_TYPE_LABELS[doc_type]
    specs = templates.template_sections(doc_type, subject)
    return {
        "document_type": doc_type,
        "title": f"{label}: {templates.title_case(subject)}",
        "subtitle": f"An autonomously prepared {label.lower()}",
        "audience": "Business stakeholders and decision-makers",
        "tone": "professional",
        "assumptions": [
            "Specific figures, names and dates are illustrative mock data where the "
            "request did not provide them.",
            f"The request was interpreted as a {label.lower()} about "
            f"“{subject}”.",
        ],
        "sections": [s.model_dump() for s in specs],
    }


def _coerce_sections(raw_sections: Any, subject: str) -> List[SectionSpec]:
    specs: List[SectionSpec] = []
    if not isinstance(raw_sections, list):
        return specs
    valid_types = {"paragraph", "bullets", "table", "subheading"}
    for item in raw_sections:
        if not isinstance(item, dict) or not item.get("heading"):
            continue
        cts = [c for c in (item.get("content_types") or []) if c in valid_types]
        specs.append(
            SectionSpec(
                heading=str(item["heading"]).strip(),
                purpose=str(item.get("purpose", "")).strip(),
                content_types=cts or ["paragraph"],
                key_points=[str(k) for k in (item.get("key_points") or [])][:8],
            )
        )
    return specs


def _build_tasks(sections: List[SectionSpec]) -> List[Task]:
    """Derive the agent's TODO list from the chosen sections."""
    tasks: List[Task] = [
        Task(id=1, title="Interpret request and classify document type",
             detail="Determine intent, document type, audience and tone."),
        Task(id=2, title="Design document outline",
             detail="Decide the sections and the content each should carry."),
    ]
    next_id = 3
    for spec in sections:
        tasks.append(
            Task(id=next_id, title=f"Draft section: {spec.heading}",
                 detail=spec.purpose or "Draft section content.")
        )
        next_id += 1
    tasks.append(Task(id=next_id, title="Self-review and refine draft",
                      detail="Run a quality self-check and revise weak sections."))
    tasks.append(Task(id=next_id + 1, title="Render polished .docx document",
                      detail="Format the content into a styled Word document."))
    return tasks


class Planner:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    def plan(self, request: str) -> Tuple[DocumentPlan, str]:
        subject = templates.derive_subject(request)
        data, provider = self.llm.generate_json(
            _PLANNER_SYSTEM,
            _PLANNER_USER_TMPL.format(request=request, types="\n".join(f"- {t}" for t in _ALLOWED_TYPES)),
            fallback=lambda: _heuristic_plan_dict(request),
            temperature=0.3,
        )

        doc_type = str(data.get("document_type", "")).strip()
        if doc_type not in _ALLOWED_TYPES:
            doc_type = templates.detect_document_type(request)

        sections = _coerce_sections(data.get("sections"), subject)
        if len(sections) < 3:  # LLM under-produced -> fall back to a solid outline
            sections = templates.template_sections(doc_type, subject)
            if provider != "heuristic-fallback":
                provider = f"{provider}+template-repair"

        label = templates.DOC_TYPE_LABELS[doc_type]
        plan = DocumentPlan(
            document_type=doc_type,
            title=str(data.get("title") or f"{label}: {templates.title_case(subject)}").strip(),
            subtitle=str(data.get("subtitle") or "").strip(),
            audience=str(data.get("audience") or "Business stakeholders and decision-makers").strip(),
            tone=str(data.get("tone") or "professional").strip(),
            date=tools.get("today")(),
            sections=sections,
            tasks=_build_tasks(sections),
            assumptions=[str(a) for a in (data.get("assumptions") or [])][:6],
        )
        return plan, provider

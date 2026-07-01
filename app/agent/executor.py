"""Execution stage: draft the content for every planned section.

Each section is drafted independently. When an LLM is configured the executor
asks it for structured JSON blocks; otherwise (or on malformed output) it
synthesises professional prose and realistic tables from the template knowledge
base and mock-data tools. The same machinery powers the reflection-driven
revision pass (`revise`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from ..llm import LLMClient
from ..schemas import Block, DocumentPlan, ReflectionResult, SectionContent, SectionSpec
from . import templates
from .tools import tools

_TABLE_CATEGORIES = {
    "timeline", "budget", "risk", "action", "roles", "metrics",
    "meeting_meta", "components", "api", "data_model", "requirements", "revision",
}

_EXEC_SYSTEM = (
    "You are a senior business/technical writer. You draft one section of a "
    "polished document at a time and respond only with a single valid JSON object."
)

_EXEC_USER_TMPL = """\
Draft the section below for a {doc_label} titled "{title}".
Audience: {audience}. Tone: {tone}. Overall subject: {subject}.

SECTION:
  heading: {heading}
  purpose: {purpose}
  suggested content types: {content_types}
  key points to cover: {key_points}
{critique}
Respond with JSON:
{{
  "heading": "{heading}",
  "blocks": [
    {{"type": "paragraph", "text": "..."}},
    {{"type": "subheading", "text": "..."}},
    {{"type": "bullets", "items": ["...", "..."]}},
    {{"type": "table", "caption": "...", "columns": ["..."], "rows": [["..."]]}}
  ]
}}

Rules:
- 1-2 short paragraphs plus bullets and/or a table as appropriate.
- Use a "table" block for schedules, budgets, risks, action items, metrics, roles.
- Invent realistic illustrative data where specifics are unknown.
- Keep it concise and professional; no markdown, no placeholders like [TODO].
"""


@dataclass
class _Ctx:
    subject: str
    audience: str
    doc_label: str
    tone: str


# --------------------------------------------------------------------------- #
# Deterministic prose helpers
# --------------------------------------------------------------------------- #
_SPECIFIC_PARAS = {
    "executive summary": "This {doc_label_lower} presents a concise overview of {subject} for {audience}. "
        "It sets out the context, the recommended approach and the anticipated outcomes so readers can make "
        "an informed decision quickly.",
    "overview": "This document describes {subject} and the reasoning behind the chosen approach. It is intended "
        "for {audience} and provides the context needed to understand the sections that follow.",
    "purpose": "The purpose of this procedure is to ensure that {subject} is carried out consistently, safely and "
        "efficiently. It defines the standard steps to be followed by {audience}.",
    "conclusion": "In summary, the analysis of {subject} supports the recommendations set out above. Acting on them "
        "positions {audience} to capture the identified benefits while managing the associated risks.",
    "next steps": "To proceed, {audience} should review and approve this document, after which the team will "
        "initiate the activities outlined above.",
    "problem statement": "Today, {subject} presents several challenges that constrain efficiency and outcomes. This "
        "section frames those problems and the opportunity that addressing them represents.",
    "introduction": "This section provides the background and context for {subject}, clarifying the scope of the "
        "document and the needs of {audience}.",
}

_CATEGORY_PARAS = {
    "timeline": "The work is organised into clearly defined phases, each with its own objectives, activities and "
        "owner. The schedule below is indicative and can be refined once scope is confirmed.",
    "budget": "The commercial summary below breaks the engagement into discrete line items. Figures are indicative "
        "and would be confirmed following a detailed scoping exercise.",
    "risk": "The following risks have been identified, together with their likelihood, potential impact and the "
        "mitigations that will be put in place to manage them.",
    "action": "The action items below capture the agreed follow-ups, each with a clear owner and due date to ensure "
        "accountability.",
    "roles": "The table below sets out the key roles and their primary responsibilities to avoid ambiguity and "
        "overlap.",
    "metrics": "Progress will be measured against the metrics below, each with a target and a defined measurement "
        "method.",
    "meeting_meta": "The key details of the meeting are summarised below.",
    "components": "The solution is composed of the major components below, each with a well-defined responsibility.",
    "api": "The primary interfaces exposed by the system are summarised below.",
    "data_model": "The core data entities and their relationships are described below.",
    "requirements": "The prioritised requirements are listed below using MoSCoW prioritisation to focus delivery.",
    "personas": "This document targets the user personas below, whose needs shape the requirements.",
    "generic": "This section addresses {heading_lower} in the context of {subject}, giving {audience} the relevant "
        "detail and considerations.",
}


def _para_text(category: str, heading: str, ctx: _Ctx) -> str:
    key = heading.lower().strip()
    tmpl = _SPECIFIC_PARAS.get(key) or _CATEGORY_PARAS.get(category) or _CATEGORY_PARAS["generic"]
    return tmpl.format(
        subject=ctx.subject,
        audience=ctx.audience,
        doc_label_lower=ctx.doc_label.lower(),
        heading_lower=heading.lower(),
    )


def _mock_bullets(heading: str, ctx: _Ctx) -> List[str]:
    h = heading.lower()
    s = ctx.subject
    table = {
        "attend": ["Project Sponsor", "Project Lead", "Solution Architect",
                   "Engineering representative", "QA representative"],
        "agenda": [f"Review objectives for {s}", "Review current status and blockers",
                   "Agree next steps and owners", "Any other business"],
        "deliverable": [f"Approved plan and scope for {s}", "Implemented solution",
                        "Test evidence and documentation", "Training and handover materials"],
        "prerequisit": ["Access to required systems and tools", "Approved inputs and reference data",
                        "Relevant permissions granted", "Key stakeholders identified and available"],
        "decision": [f"Approved the proposed approach for {s}", "Agreed the timeline and milestones",
                     "Confirmed owners for each workstream"],
        "objective": [f"Deliver {s} on time and within budget", "Meet the agreed quality standards",
                      "Achieve measurable business impact"],
        "goal": [f"Deliver {s} to the agreed standard", "Realise the intended business value",
                 "Establish a sustainable operating model"],
        "recommendation": [f"Proceed with the recommended approach to {s}",
                           "Allocate the required resources and owners",
                           "Establish success metrics and a review cadence"],
        "scope": [f"In scope: core delivery of {s}", "In scope: documentation and handover",
                  "Out of scope: unrelated systems and processes",
                  "Out of scope: ongoing operations beyond the support window"],
        "safety": ["Follow all applicable safety and compliance policies",
                   "Escalate any incidents immediately", "Record deviations and corrective actions"],
        "record": ["Store outputs in the designated system of record",
                   "Retain evidence per the retention policy", "Ensure entries are auditable"],
        "open question": [f"What is the confirmed budget and timeline for {s}?",
                          "Who is the accountable executive sponsor?",
                          "Are there compliance or regulatory constraints to consider?"],
        "persona": ["Primary user who interacts with the product daily",
                    "Decision-maker who approves adoption",
                    "Administrator responsible for configuration and support"],
        "experience": [f"Discovery: the user learns about {s}",
                       "Core task: the user completes the primary workflow",
                       "Follow-up: the user reviews results and takes next actions"],
        "non-goal": [f"Goal: deliver a reliable solution for {s}",
                     "Goal: keep the design simple and observable",
                     "Non-goal: solving adjacent problems out of scope",
                     "Non-goal: premature optimisation"],
    }
    for needle, items in table.items():
        if needle in h:
            return items
    return [
        f"Key considerations for {heading.lower()} relating to {s}",
        "Dependencies, owners and assumptions",
        "Actions required to move forward",
    ]


def _heuristic_section(spec: SectionSpec, ctx: _Ctx) -> SectionContent:
    category = tools.get("classify_heading")(spec.heading)
    blocks: List[Block] = [Block(type="paragraph", text=_para_text(category, spec.heading, ctx))]

    wants_table = "table" in spec.content_types or category in _TABLE_CATEGORIES
    wants_bullets = "bullets" in spec.content_types or bool(spec.key_points)
    wants_subheads = "subheading" in spec.content_types

    if wants_subheads:
        for sub in ("Key Points", "Considerations"):
            blocks.append(Block(type="subheading", text=sub))
            blocks.append(Block(type="paragraph", text=_para_text("generic", f"{sub} for {spec.heading}", ctx)))

    if wants_bullets:
        items = spec.key_points if spec.key_points else _mock_bullets(spec.heading, ctx)
        blocks.append(Block(type="bullets", items=items))

    if wants_table:
        t = tools.get("mock_table")(category, spec.heading, ctx.subject)
        blocks.append(
            Block(type="table", columns=t["columns"], rows=t["rows"], caption=t.get("caption") or None)
        )

    return SectionContent(heading=spec.heading, blocks=blocks)


# --------------------------------------------------------------------------- #
# LLM output coercion
# --------------------------------------------------------------------------- #
def _coerce_blocks(raw: Any) -> List[Block]:
    blocks: List[Block] = []
    if not isinstance(raw, list):
        return blocks
    for item in raw:
        if not isinstance(item, dict):
            continue
        btype = item.get("type")
        try:
            if btype in ("paragraph", "subheading") and item.get("text"):
                blocks.append(Block(type=btype, text=str(item["text"]).strip()))
            elif btype == "bullets" and item.get("items"):
                items = [str(i).strip() for i in item["items"] if str(i).strip()]
                if items:
                    blocks.append(Block(type="bullets", items=items))
            elif btype == "table" and item.get("columns") and item.get("rows"):
                cols = [str(c) for c in item["columns"]]
                rows = [[str(c) for c in row] for row in item["rows"] if isinstance(row, list)]
                if rows:
                    blocks.append(
                        Block(type="table", columns=cols, rows=rows,
                              caption=str(item.get("caption") or "").strip() or None)
                    )
        except Exception:  # noqa: BLE001 - skip a malformed block, keep the rest
            continue
    return blocks


class Executor:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    def _ctx(self, plan: DocumentPlan) -> _Ctx:
        return _Ctx(
            subject=templates.derive_subject(plan.title),
            audience=plan.audience,
            doc_label=templates.DOC_TYPE_LABELS.get(plan.document_type, "document"),
            tone=plan.tone,
        )

    def _draft(self, plan: DocumentPlan, spec: SectionSpec, ctx: _Ctx, critique: str = "") -> Tuple[SectionContent, str]:
        user = _EXEC_USER_TMPL.format(
            doc_label=ctx.doc_label, title=plan.title, audience=plan.audience, tone=plan.tone,
            subject=ctx.subject, heading=spec.heading, purpose=spec.purpose,
            content_types=", ".join(spec.content_types), key_points="; ".join(spec.key_points) or "(use judgement)",
            critique=f"\nReviewer feedback to address: {critique}\n" if critique else "",
        )
        data, provider = self.llm.generate_json(
            _EXEC_SYSTEM, user,
            fallback=lambda: {"heading": spec.heading, "blocks": []},
            temperature=0.5,
        )
        blocks = _coerce_blocks(data.get("blocks"))
        if not blocks:  # LLM produced nothing usable -> deterministic draft
            return _heuristic_section(spec, ctx), "heuristic-fallback"
        return SectionContent(heading=spec.heading, blocks=blocks), provider

    def execute(self, plan: DocumentPlan) -> Tuple[List[SectionContent], List[str]]:
        ctx = self._ctx(plan)
        contents: List[SectionContent] = []
        providers: List[str] = []
        for spec in plan.sections:
            content, provider = self._draft(plan, spec, ctx)
            contents.append(content)
            providers.append(provider)
        return contents, providers

    def revise(
        self, plan: DocumentPlan, contents: List[SectionContent], reflection: ReflectionResult
    ) -> List[SectionContent]:
        """Re-draft the sections flagged by the reflection stage (one pass)."""
        ctx = self._ctx(plan)
        targets = {h.lower() for h in reflection.sections_to_improve}
        spec_by_heading = {s.heading.lower(): s for s in plan.sections}
        critique = " ".join(reflection.suggestions[:3])

        revised: List[SectionContent] = []
        for content in contents:
            if content.heading.lower() in targets:
                spec = spec_by_heading.get(content.heading.lower())
                if spec is not None:
                    new_content, _ = self._draft(plan, spec, ctx, critique=critique)
                    revised.append(self._merge_stronger(content, new_content, spec, ctx))
                    continue
            revised.append(content)
        return revised

    @staticmethod
    def _merge_stronger(old: SectionContent, new: SectionContent, spec: SectionSpec, ctx: _Ctx) -> SectionContent:
        """Keep whichever draft is richer, then guarantee a minimum substance."""
        chosen = new if new.word_count() >= old.word_count() else old
        # Guarantee the section is not threadbare after revision.
        if chosen.word_count() < 40:
            enrich = _heuristic_section(spec, ctx)
            chosen = SectionContent(heading=chosen.heading, blocks=chosen.blocks + enrich.blocks)
        return chosen

"""Reflection / self-check stage — the agent's mandatory engineering improvement.

Before finalising, the agent critiques its own draft against the plan. This
catches the classic failure modes of generative pipelines — missing sections,
threadbare content, empty tables, placeholder text — *before* they reach the
user, and feeds concrete, section-level feedback into a bounded revision pass.

Two layers run together:
  1. Deterministic structural checks (always on, fast, reliable).
  2. Optional LLM qualitative critique (when a provider is configured), merged
     in to catch issues rules can't see (clarity, relevance, tone).
"""
from __future__ import annotations

from typing import List, Tuple

from ..llm import LLMClient
from ..schemas import DocumentPlan, ReflectionResult, SectionContent

_MIN_SECTION_WORDS = 25
_MIN_DOC_WORDS = 250

_PLACEHOLDERS = ("lorem ipsum", "[todo]", "todo:", "tbd", "xxxx", "placeholder", "<insert")

_REFLECT_SYSTEM = (
    "You are a meticulous editor performing QA on a business document draft. "
    "You respond only with a single valid JSON object."
)

_REFLECT_USER_TMPL = """\
Assess whether this draft fully and professionally satisfies the plan.

DOCUMENT: {title} ({doc_type})
PLANNED SECTIONS: {planned}

DRAFT (heading — word count — opening):
{draft}

Return JSON:
{{
  "passed": <true|false>,
  "quality_score": <0-100>,
  "issues": ["<specific problems found>"],
  "suggestions": ["<concrete improvements>"],
  "sections_to_improve": ["<exact headings that need work>"]
}}
Judge completeness, clarity, relevance to the request, and whether any section
is thin, generic or contains placeholders. Be strict but fair.
"""


def _structural_check(plan: DocumentPlan, contents: List[SectionContent]) -> ReflectionResult:
    issues: List[str] = []
    suggestions: List[str] = []
    to_improve: List[str] = []
    score = 100

    by_heading = {c.heading.lower(): c for c in contents}

    # 1. Every planned section must be present.
    for spec in plan.sections:
        if spec.heading.lower() not in by_heading:
            issues.append(f"Planned section '{spec.heading}' is missing from the draft.")
            to_improve.append(spec.heading)
            score -= 12

    # 2. Per-section quality.
    for content in contents:
        wc = content.word_count()
        if wc < _MIN_SECTION_WORDS:
            issues.append(f"Section '{content.heading}' is too thin ({wc} words).")
            to_improve.append(content.heading)
            score -= 8
        for block in content.blocks:
            if block.type == "table" and not block.rows:
                issues.append(f"Table in section '{content.heading}' has no rows.")
                to_improve.append(content.heading)
                score -= 5
            text = " ".join(filter(None, [block.text or "", " ".join(block.items or [])])).lower()
            if any(p in text for p in _PLACEHOLDERS):
                issues.append(f"Section '{content.heading}' contains placeholder text.")
                to_improve.append(content.heading)
                score -= 6

    # 3. Whole-document length.
    total_words = sum(c.word_count() for c in contents)
    if total_words < _MIN_DOC_WORDS:
        issues.append(f"Overall document is short ({total_words} words).")
        suggestions.append("Expand the thinnest sections with more specific detail.")
        score -= 10

    if to_improve:
        suggestions.append(
            "Add concrete detail, examples or data to: " + ", ".join(sorted(set(to_improve))) + "."
        )

    score = max(0, min(100, score))
    passed = not issues and score >= 80
    return ReflectionResult(
        passed=passed,
        quality_score=score,
        issues=issues,
        suggestions=suggestions,
        sections_to_improve=sorted(set(to_improve)),
    )


def _render_draft(contents: List[SectionContent]) -> str:
    lines = []
    for c in contents:
        opening = ""
        for b in c.blocks:
            if b.type in ("paragraph", "subheading") and b.text:
                opening = b.text[:160]
                break
        lines.append(f"- {c.heading} — {c.word_count()}w — {opening}")
    return "\n".join(lines)


class Reflector:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    def review(self, plan: DocumentPlan, contents: List[SectionContent]) -> Tuple[ReflectionResult, str]:
        base = _structural_check(plan, contents)

        if not self.llm.has_llm:
            return base, "heuristic-fallback"

        # Augment with an LLM critique and merge (union of concerns, min score).
        data, provider = self.llm.generate_json(
            _REFLECT_SYSTEM,
            _REFLECT_USER_TMPL.format(
                title=plan.title,
                doc_type=plan.document_type,
                planned=", ".join(s.heading for s in plan.sections),
                draft=_render_draft(contents),
            ),
            fallback=lambda: base.model_dump(),
            temperature=0.2,
        )
        try:
            llm_view = ReflectionResult(
                passed=bool(data.get("passed", base.passed)),
                quality_score=int(data.get("quality_score", base.quality_score)),
                issues=[str(i) for i in (data.get("issues") or [])][:8],
                suggestions=[str(s) for s in (data.get("suggestions") or [])][:8],
                sections_to_improve=[str(s) for s in (data.get("sections_to_improve") or [])][:8],
            )
        except Exception:  # noqa: BLE001
            return base, provider

        merged = ReflectionResult(
            passed=base.passed and llm_view.passed,
            quality_score=min(base.quality_score, llm_view.quality_score),
            issues=list(dict.fromkeys(base.issues + llm_view.issues)),
            suggestions=list(dict.fromkeys(base.suggestions + llm_view.suggestions)),
            sections_to_improve=list(dict.fromkeys(base.sections_to_improve + llm_view.sections_to_improve)),
        )
        return merged, provider

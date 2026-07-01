"""Run the two required demo scenarios and save the generated documents.

Usage:
    python -m examples.run_examples

Works fully offline (deterministic engine). If you set GROQ_API_KEY /
GEMINI_API_KEY (or enable Ollama) the same code path uses the LLM instead.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Render unicode (em-dashes, smart quotes) correctly even on Windows consoles
# that default to a legacy code page (cp1252) — otherwise the demo output, and
# the video recorded from it, shows mojibake ("�") instead of the real glyphs.
try:  # Python 3.7+; best-effort, never fail the demo over console encoding
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover - non-reconfigurable stream
    pass

from app.agent import AgentOrchestrator

# 1) Standard business request.
STANDARD = "Write a business proposal for an AI-powered customer support chatbot for a mid-sized e-commerce company."

# 2) Complex / ambiguous request — missing format, timeline and conflicting goals.
COMPLEX = (
    "We have a leadership offsite coming up and need a document about speeding up our "
    "product delivery without sacrificing quality or burning out the team. Budget is "
    "tight, timeline is unclear, and leadership hasn't agreed on the format — decide "
    "the best structure yourself and make reasonable assumptions."
)


def _divider(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def run_case(label: str, request: str, out_dir: Path) -> None:
    _divider(label)
    print(f"REQUEST:\n  {request}\n")

    orch = AgentOrchestrator()
    result = orch.run(request)

    print(f"DECIDED DOCUMENT TYPE : {result.plan.document_type}")
    print(f"TITLE                 : {result.plan.title}")
    print(f"LLM PROVIDER          : {result.llm_provider}")
    print(f"ELAPSED               : {result.elapsed}s\n")

    print("AGENT-GENERATED TASK LIST (TODO):")
    for task in result.plan.tasks:
        mark = {"done": "[x]", "in_progress": "[~]"}.get(task.status, "[ ]")
        print(f"  {mark} {task.id:>2}. {task.title}")

    if result.plan.assumptions:
        print("\nASSUMPTIONS MADE:")
        for a in result.plan.assumptions:
            print(f"  - {a}")

    print("\nSELF-CHECK (reflection):")
    print(f"  quality_score = {result.reflection.quality_score}/100  "
          f"passed = {result.reflection.passed}  revised = {result.reflection.revised}")
    for issue in result.reflection.issues:
        print(f"  ! {issue}")

    print("\nEXECUTION TRACE:")
    for step in result.trace:
        prov = f" via {step.provider}" if step.provider else ""
        print(f"  - {step.step:<10} {step.status}{prov}: {step.detail}")

    print(f"\nSUMMARY:\n  {result.summary}")

    out_dir.mkdir(parents=True, exist_ok=True)
    filename = "".join(c if c.isalnum() or c in " -_" else "" for c in result.plan.title)
    filename = "_".join(filename.split())[:60] + ".docx"
    path = out_dir / filename
    path.write_bytes(result.docx_bytes)
    print(f"\nSAVED DOCUMENT -> {path.resolve()}  ({len(result.docx_bytes):,} bytes)")


def main() -> None:
    out_dir = Path("generated")
    run_case("TEST CASE 1 — STANDARD BUSINESS REQUEST", STANDARD, out_dir)
    run_case("TEST CASE 2 — COMPLEX / AMBIGUOUS REQUEST", COMPLEX, out_dir)
    print("\nDone. Open the .docx files in the 'generated/' folder.\n")


if __name__ == "__main__":
    main()

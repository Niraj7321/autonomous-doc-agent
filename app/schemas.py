"""Pydantic models shared across the agent pipeline and the HTTP API.

These models are the single source of truth for the data that flows between the
planner -> executor -> reflector -> document builder, and also define the
request/response contract of the FastAPI service.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Content building blocks
# --------------------------------------------------------------------------- #

BlockType = Literal["paragraph", "subheading", "bullets", "table"]


class Block(BaseModel):
    """A single renderable unit inside a section.

    A block is a small tagged union: `type` decides which of the optional
    fields are meaningful. Keeping it flat (instead of a real union) makes the
    JSON that an LLM must emit far simpler and easier to validate/repair.
    """

    type: BlockType
    text: Optional[str] = None              # paragraph / subheading
    items: Optional[List[str]] = None       # bullets
    columns: Optional[List[str]] = None     # table header
    rows: Optional[List[List[str]]] = None  # table body
    caption: Optional[str] = None           # table caption


class SectionContent(BaseModel):
    """Fully drafted content for one section of the document."""

    heading: str
    blocks: List[Block] = Field(default_factory=list)

    def word_count(self) -> int:
        total = 0
        for b in self.blocks:
            if b.text:
                total += len(b.text.split())
            if b.items:
                total += sum(len(i.split()) for i in b.items)
            if b.rows:
                total += sum(len(str(c).split()) for row in b.rows for c in row)
        return total


# --------------------------------------------------------------------------- #
# Plan
# --------------------------------------------------------------------------- #


class SectionSpec(BaseModel):
    """The planner's intent for a section (before it is drafted)."""

    heading: str
    purpose: str = ""
    content_types: List[BlockType] = Field(default_factory=lambda: ["paragraph"])
    key_points: List[str] = Field(default_factory=list)


class Task(BaseModel):
    """One item in the agent's self-generated TODO list."""

    id: int
    title: str
    detail: str = ""
    status: Literal["pending", "in_progress", "done"] = "pending"


class DocumentPlan(BaseModel):
    """The agent's execution plan for a single request."""

    document_type: str
    title: str
    subtitle: str = ""
    audience: str = "General business stakeholders"
    tone: str = "professional"
    date: str = ""
    sections: List[SectionSpec] = Field(default_factory=list)
    tasks: List[Task] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Reflection / self-check
# --------------------------------------------------------------------------- #


class ReflectionResult(BaseModel):
    """Output of the self-check stage."""

    passed: bool
    quality_score: int = Field(0, ge=0, le=100)
    issues: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    sections_to_improve: List[str] = Field(default_factory=list)
    revised: bool = False


# --------------------------------------------------------------------------- #
# Trace / observability
# --------------------------------------------------------------------------- #


class TraceStep(BaseModel):
    step: str
    status: str
    detail: str = ""
    provider: Optional[str] = None


# --------------------------------------------------------------------------- #
# API request / response
# --------------------------------------------------------------------------- #


class AgentRequest(BaseModel):
    request: str = Field(..., description="Natural-language description of the document to produce.")
    include_base64: bool = Field(
        False, description="If true, also return the .docx inline as base64 in the response."
    )

    # Pre-fill Swagger's "Try it out" body with a realistic, ready-to-run request
    # instead of the default {"request": "string"}.
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "request": (
                        "Write a business proposal for an AI-powered customer support "
                        "chatbot for a mid-sized e-commerce company."
                    ),
                    "include_base64": False,
                }
            ]
        }
    }


class DocumentInfo(BaseModel):
    id: str
    filename: str
    download_url: str
    size_bytes: int
    word_count: int
    base64: Optional[str] = None


class AgentResponse(BaseModel):
    request: str
    document_type: str
    title: str
    summary: str
    plan: DocumentPlan
    reflection: ReflectionResult
    document: DocumentInfo
    llm_provider: str
    elapsed_seconds: float
    trace: List[TraceStep] = Field(default_factory=list)

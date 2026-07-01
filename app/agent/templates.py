"""Document-type knowledge base for the deterministic (offline) engine.

This module encodes *what a good X looks like* for each supported document
type: which sections belong in it, their purpose, and the kind of content they
carry. The planner uses it as a fallback when no LLM is available; the executor
uses the section hints to decide when to synthesise a table vs. prose.
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from ..schemas import SectionSpec

# doc_type -> human-friendly label used in titles
DOC_TYPE_LABELS: Dict[str, str] = {
    "proposal": "Proposal",
    "meeting_minutes": "Meeting Minutes",
    "project_plan": "Project Plan",
    "business_report": "Business Report",
    "technical_design": "Technical Design Document",
    "sop": "Standard Operating Procedure",
    "product_spec": "Product Specification",
}

# Ordered keyword -> doc_type rules (first match wins).
_DETECTION_RULES: List[Tuple[str, str]] = [
    (r"\bmeeting\s+minutes\b|\bminutes\b|\bmom\b", "meeting_minutes"),
    (r"\bstandard operating procedure\b|\bsop\b|\bprocedure\b|\bwork instruction\b", "sop"),
    (r"\bproduct spec\w*|\bprd\b|\bfeature spec\w*|\brequirements? doc", "product_spec"),
    (r"\btechnical design\b|\bdesign doc\w*|\barchitecture\b|\bhld\b|\blld\b|\brfc\b", "technical_design"),
    (r"\bproject plan\b|\broadmap\b|\bproject charter\b|\bexecution plan\b", "project_plan"),
    (r"\bproposal\b|\bpitch\b|\bstatement of work\b|\bsow\b|\bquote\b|\bquotation\b", "proposal"),
    (r"\breport\b|\banalysis\b|\breview\b|\bassessment\b|\bwhitepaper\b", "business_report"),
]

_STOP_PREFIX = re.compile(
    r"^\s*(please\s+)?(can you\s+|could you\s+|i (?:need|want|would like)\s+(?:you to\s+)?)?"
    r"(write|create|draft|generate|prepare|produce|make|build|compose|put together|develop)\s+"
    r"(me\s+)?(a|an|the|some)?\s*",
    re.IGNORECASE,
)


def detect_document_type(request: str) -> str:
    text = request.lower()
    for pattern, doc_type in _DETECTION_RULES:
        if re.search(pattern, text):
            return doc_type
    return "business_report"  # sensible default for open-ended asks


_ABOUT_CLAUSE = re.compile(
    r"\b(?:about|regarding|concerning|covering|on the topic of|to discuss)\s+(.+)",
    re.IGNORECASE,
)
_LEADING_FILLER = re.compile(
    r"^(?:we|i|they|our team)\s+(?:have|need|want|would like|are looking for|require)\b"
    r".*?\b(?:document|report|deck|paper|memo|brief|note|plan|proposal|something)\b\s*",
    re.IGNORECASE,
)
# Optional adjective(s) before a document-type noun, then a preposition.
_DOCTYPE_PREFIX = re.compile(
    r"^(?:\w+\s+){0,2}?(?:proposal|report|plan|charter|minutes|design(?: document)?|"
    r"doc(?:ument)?|spec(?:ification)?|prd|sop|procedure|overview|analysis|assessment|"
    r"review|whitepaper)\s+(?:for|on|about|regarding|to|of|covering)\s+",
    re.IGNORECASE,
)


def derive_subject(request: str) -> str:
    """Turn a raw request into a concise noun-phrase subject for titling."""
    text = re.sub(r"\s+", " ", request.strip())

    about = _ABOUT_CLAUSE.search(text)
    if about:  # "...a document about X" -> X
        subject = about.group(1)
    else:
        subject = _STOP_PREFIX.sub("", text)  # strip "write a / create an ..."
        subject = _LEADING_FILLER.sub("", subject)
        subject = _DOCTYPE_PREFIX.sub("", subject)  # strip "proposal for ..."

    # Cut at the first sentence / clause boundary so trailing instructions drop off.
    subject = re.split(r"[.\n;]|\s[—–-]\s|\bthat\b|\bwhich\b", subject, maxsplit=1)[0]
    subject = re.sub(r"^(?:a|an|the|some|our|your|my)\s+", "", subject, flags=re.IGNORECASE)
    subject = re.sub(r"\s+", " ", subject).strip(" .,;:\"'")

    if not subject or len(subject) < 3:
        subject = _STOP_PREFIX.sub("", text).strip(" .") or "the requested initiative"

    words = subject.split()
    if len(words) > 12:  # keep titles readable, without a mid-word ellipsis
        words = words[:12]
    # Drop dangling connective words so a truncated title reads cleanly.
    _trailing = {"the", "a", "an", "and", "or", "of", "to", "for", "in", "on",
                 "with", "by", "at", "our", "your", "their"}
    while words and words[-1].lower() in _trailing:
        words.pop()
    return " ".join(words) or "the requested initiative"


def title_case(subject: str) -> str:
    small = {"a", "an", "and", "or", "nor", "but", "the", "for", "of", "to",
             "in", "on", "with", "at", "by", "as", "per", "via", "vs"}
    words = subject.split()
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        if i != 0 and lw in small:
            out.append(lw)
        elif w.isupper() and len(w) <= 5:  # keep acronyms (API, SaaS-ish)
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


# --------------------------------------------------------------------------- #
# Section templates. Each entry: (heading, purpose, content_types, key_points)
# key_points may contain "{subject}" which is filled per request.
# --------------------------------------------------------------------------- #
_T: Dict[str, List[Tuple[str, str, List[str], List[str]]]] = {
    "proposal": [
        ("Executive Summary", "Summarise the opportunity and recommendation", ["paragraph"],
         ["Overview of {subject}", "Value proposition", "Recommended engagement"]),
        ("Background & Problem Statement", "Frame the current situation and pain points", ["paragraph", "bullets"],
         ["Current challenges around {subject}", "Business impact", "Why act now"]),
        ("Proposed Solution", "Describe the approach and how it solves the problem", ["paragraph", "bullets"],
         ["Solution overview", "Key capabilities", "Differentiators"]),
        ("Scope of Work", "Define deliverables and boundaries", ["bullets"],
         ["In-scope deliverables", "Out-of-scope items", "Assumptions"]),
        ("Timeline & Milestones", "Lay out the delivery schedule", ["table"], []),
        ("Investment & Pricing", "Present the commercial proposal", ["table", "paragraph"], []),
        ("Risks & Mitigation", "Surface risks and how they are handled", ["table"], []),
        ("Success Metrics", "State how success will be measured", ["bullets"],
         ["Adoption targets", "Efficiency gains", "ROI indicators"]),
        ("Next Steps", "Tell the reader exactly what to do next", ["bullets"],
         ["Approve the proposal", "Kick-off scheduling", "Points of contact"]),
    ],
    "meeting_minutes": [
        ("Meeting Overview", "Capture the logistics of the meeting", ["table"], []),
        ("Attendees", "List who was present", ["bullets"], []),
        ("Agenda", "Record the planned discussion items", ["bullets"], []),
        ("Discussion Summary", "Summarise the key points discussed", ["paragraph", "subheading"],
         ["Topic-by-topic notes on {subject}"]),
        ("Decisions Made", "Record the decisions reached", ["bullets"], []),
        ("Action Items", "Track follow-ups with owners and due dates", ["table"], []),
        ("Next Meeting", "State the follow-up cadence", ["paragraph"], []),
    ],
    "project_plan": [
        ("Project Overview", "Introduce the project and its context", ["paragraph"],
         ["Purpose of {subject}", "Business context", "Sponsor & stakeholders"]),
        ("Objectives & Goals", "State measurable objectives", ["bullets"],
         ["Primary objective", "Secondary objectives", "Definition of done"]),
        ("Scope", "Define what is in and out of scope", ["bullets"],
         ["In scope", "Out of scope", "Key assumptions"]),
        ("Deliverables", "List the tangible outputs", ["bullets"], []),
        ("Timeline & Phases", "Break work into phases with dates", ["table"], []),
        ("Resource Plan", "Map roles to responsibilities", ["table"], []),
        ("Milestones", "Highlight key checkpoints", ["table"], []),
        ("Risks & Mitigation", "Identify and plan for risks", ["table"], []),
        ("Success Criteria", "Define how success is judged", ["bullets"],
         ["Delivery targets", "Quality gates", "Stakeholder acceptance"]),
    ],
    "business_report": [
        ("Executive Summary", "Give leaders the bottom line up front", ["paragraph"],
         ["Purpose of this report on {subject}", "Headline findings", "Top recommendation"]),
        ("Introduction & Background", "Provide context for the analysis", ["paragraph"],
         ["Context around {subject}", "Scope of the report", "Audience"]),
        ("Methodology", "Explain how the analysis was performed", ["paragraph", "bullets"],
         ["Data sources", "Analytical approach", "Limitations"]),
        ("Key Findings", "Present the main findings with evidence", ["bullets", "table"], []),
        ("Analysis & Discussion", "Interpret what the findings mean", ["paragraph", "subheading"],
         ["Trends and drivers", "Implications for the business"]),
        ("Recommendations", "Offer actionable recommendations", ["bullets"],
         ["Short-term actions", "Medium-term actions", "Owners"]),
        ("Conclusion", "Close with a concise wrap-up", ["paragraph"], []),
    ],
    "technical_design": [
        ("Overview", "Summarise the system and this document", ["paragraph"],
         ["What {subject} is", "Motivation", "Summary of the design"]),
        ("Goals & Non-Goals", "Bound the problem explicitly", ["bullets"],
         ["Goals", "Non-goals", "Assumptions"]),
        ("Requirements", "Capture functional & non-functional needs", ["bullets"],
         ["Functional requirements", "Non-functional requirements", "Constraints"]),
        ("Proposed Architecture", "Describe the high-level design", ["paragraph", "subheading"],
         ["Component overview for {subject}", "Request flow", "Key design decisions"]),
        ("Components", "Enumerate the major components", ["table"], []),
        ("Data Model", "Describe data entities and storage", ["paragraph", "table"], []),
        ("API Design", "Define the primary interfaces", ["table"], []),
        ("Security & Privacy", "Address auth, data protection, compliance", ["bullets"],
         ["Authentication & authorization", "Data protection", "Compliance considerations"]),
        ("Scalability & Performance", "Describe how the system scales", ["paragraph", "bullets"],
         ["Expected load", "Scaling strategy", "Performance targets"]),
        ("Risks & Tradeoffs", "Be explicit about tradeoffs", ["table"], []),
        ("Rollout Plan", "Describe deployment and rollback", ["bullets"],
         ["Phased rollout", "Feature flags", "Rollback strategy"]),
        ("Open Questions", "List unresolved decisions", ["bullets"], []),
    ],
    "sop": [
        ("Purpose", "State why this procedure exists", ["paragraph"],
         ["Objective of the {subject} procedure", "Expected outcome"]),
        ("Scope", "Define where the procedure applies", ["paragraph", "bullets"],
         ["Applies to", "Does not apply to"]),
        ("Roles & Responsibilities", "Clarify who does what", ["table"], []),
        ("Prerequisites", "List required tools, access, and inputs", ["bullets"], []),
        ("Procedure", "Provide the step-by-step instructions", ["subheading", "bullets"],
         ["Preparation steps", "Execution steps", "Verification steps"]),
        ("Safety & Compliance", "Call out safety and compliance notes", ["bullets"], []),
        ("Records & Documentation", "Explain what to record and where", ["bullets"], []),
        ("Revision History", "Track document versions", ["table"], []),
    ],
    "product_spec": [
        ("Overview", "Summarise the product and this spec", ["paragraph"],
         ["What {subject} is", "Vision", "Summary"]),
        ("Problem Statement", "Explain the user problem", ["paragraph", "bullets"],
         ["User pain points", "Evidence", "Opportunity"]),
        ("Goals & Success Metrics", "Define goals and how they are measured", ["bullets", "table"], []),
        ("User Personas", "Describe the target users", ["bullets"], []),
        ("Requirements", "List prioritised requirements", ["table"], []),
        ("Features & Functionality", "Describe the core features", ["paragraph", "subheading"],
         ["Core feature set for {subject}", "Nice-to-haves"]),
        ("User Experience & Flows", "Describe key user journeys", ["paragraph", "bullets"], []),
        ("Technical Considerations", "Note dependencies and constraints", ["bullets"], []),
        ("Milestones & Timeline", "Lay out the delivery plan", ["table"], []),
        ("Risks & Open Questions", "Capture risks and unknowns", ["bullets"], []),
    ],
}


def template_sections(doc_type: str, subject: str) -> List[SectionSpec]:
    entries = _T.get(doc_type, _T["business_report"])
    specs: List[SectionSpec] = []
    for heading, purpose, content_types, key_points in entries:
        specs.append(
            SectionSpec(
                heading=heading,
                purpose=purpose,
                content_types=list(content_types),
                key_points=[kp.format(subject=subject) for kp in key_points],
            )
        )
    return specs

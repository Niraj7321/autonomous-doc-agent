"""Tool registry + deterministic mock-data generators.

The executor orchestrates a handful of small, pure "tools" (date lookup,
heading classification, tabular mock-data synthesis). Registering them behind a
tiny registry keeps tool usage explicit and traceable, and makes it trivial to
add new tools later (e.g. a real data source) without touching the executor.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Callable] = {}

    def register(self, name: str):
        def deco(fn: Callable) -> Callable:
            self._tools[name] = fn
            return fn

        return deco

    def get(self, name: str) -> Callable:
        return self._tools[name]

    def names(self) -> List[str]:
        return sorted(self._tools)


tools = ToolRegistry()


@tools.register("today")
def today_iso() -> str:
    return date.today().isoformat()


@tools.register("classify_heading")
def classify_heading(heading: str) -> str:
    h = heading.lower()
    checks = [
        ("action", ("action item", "action items", "follow-up")),
        ("meeting_meta", ("meeting overview", "meeting details")),
        ("revision", ("revision history", "version history", "change log")),
        ("budget", ("budget", "pricing", "investment", "cost", "commercial")),
        ("risk", ("risk", "tradeoff", "trade-off")),
        ("timeline", ("timeline", "phase", "schedule", "roadmap", "milestone")),
        ("roles", ("role", "responsibilit", "resource", "attendee", "raci")),
        ("metrics", ("metric", "kpi", "success measure", "success metric", "measurement")),
        ("api", ("api design", "endpoint", "interface")),
        ("components", ("component", "module", "service")),
        ("data_model", ("data model", "schema", "entities")),
        ("requirements", ("requirement", "user stor", "feature")),
        ("personas", ("persona",)),
    ]
    for category, needles in checks:
        if any(n in h for n in needles):
            return category
    return "generic"


# --------------------------------------------------------------------------- #
# Mock-data generators. Each returns {"columns", "rows", "caption"}.
# Determinism: seeded from the subject so a given request always renders the
# same tables (nice for demos and tests), but different requests vary.
# --------------------------------------------------------------------------- #

def _rng(subject: str, salt: str) -> random.Random:
    return random.Random(f"{subject}|{salt}")


def _future(days: int) -> str:
    return (date.today() + timedelta(days=days)).strftime("%d %b %Y")


@tools.register("mock_table")
def mock_table(category: str, heading: str, subject: str) -> Dict[str, object]:
    rng = _rng(subject, category)

    if category == "timeline":
        phases = ["Discovery & Alignment", "Design", "Build / Implementation", "Testing & QA", "Launch & Handover"]
        cols = ["Phase", "Key Activities", "Duration", "Owner"]
        owners = ["Project Lead", "Solution Architect", "Engineering", "QA Lead", "Delivery Manager"]
        acts = [
            "Requirements gathering and stakeholder alignment",
            "Solution design and technical planning",
            "Core development and integration",
            "System, integration and UAT testing",
            "Production launch, training and handover",
        ]
        durs = ["2 weeks", "2 weeks", "4 weeks", "2 weeks", "1 week"]
        rows = [[p, a, d, o] for p, a, d, o in zip(phases, acts, durs, owners)]
        return {"columns": cols, "rows": rows, "caption": "Indicative delivery schedule (illustrative)"}

    if category == "budget":
        items = [
            ("Discovery & Solution Design", "Workshops, requirements, architecture", 1, 6000),
            ("Implementation", "Core build and integration", 1, 24000),
            ("Quality Assurance", "Testing, UAT support, hardening", 1, 8000),
            ("Deployment & Enablement", "Launch, documentation, training", 1, 5000),
            ("Support (3 months)", "Hypercare and maintenance", 3, 1500),
        ]
        cols = ["Line Item", "Description", "Qty", "Unit (USD)", "Total (USD)"]
        rows = [[n, d, str(q), f"{u:,}", f"{q * u:,}"] for n, d, q, u in items]
        total = sum(q * u for _, _, q, u in items)
        rows.append(["", "", "", "Total", f"{total:,}"])
        return {"columns": cols, "rows": rows, "caption": "Indicative investment (mock figures)"}

    if category == "risk":
        base = [
            ("Scope creep beyond agreed deliverables", "Medium", "High",
             "Change-control process with written sign-off"),
            ("Delayed stakeholder feedback", "Medium", "Medium",
             "Fixed review windows and escalation path"),
            ("Integration complexity underestimated", "Low", "High",
             "Early technical spike and buffer in schedule"),
            ("Key-person dependency", "Low", "Medium",
             "Cross-training and shared documentation"),
        ]
        rng.shuffle(base)
        cols = ["Risk", "Likelihood", "Impact", "Mitigation"]
        return {"columns": cols, "rows": [list(r) for r in base], "caption": "Risk register (illustrative)"}

    if category == "action":
        owners = ["A. Kumar", "R. Mehta", "S. Lee", "J. Alvarez", "P. Osei"]
        acts = [
            "Circulate finalised requirements document",
            "Set up project workspace and access",
            "Draft integration test plan",
            "Confirm budget approval with finance",
            "Schedule kick-off with stakeholders",
        ]
        cols = ["Action Item", "Owner", "Due Date", "Status"]
        rows = [[a, rng.choice(owners), _future(7 * (i + 1)), "Open"] for i, a in enumerate(acts[:4])]
        return {"columns": cols, "rows": rows, "caption": "Tracked action items"}

    if category == "roles":
        data = [
            ("Sponsor", "Owns outcomes, removes blockers, approves budget"),
            ("Project Lead", "Day-to-day coordination, schedule and reporting"),
            ("Solution Architect", "Technical design and key decisions"),
            ("Engineering Team", "Implementation, code review, integration"),
            ("QA Lead", "Test strategy, quality gates, UAT coordination"),
        ]
        cols = ["Role", "Responsibilities"]
        return {"columns": cols, "rows": [list(r) for r in data], "caption": "Roles and responsibilities"}

    if category == "metrics":
        data = [
            ("Adoption rate", "> 70% of target users in 90 days", "Product analytics"),
            ("Process cycle time", "-30% vs. baseline", "Ops dashboard"),
            ("Customer satisfaction (CSAT)", "> 4.3 / 5", "Post-interaction survey"),
            ("Return on investment", "Payback < 9 months", "Finance review"),
        ]
        cols = ["Metric", "Target", "Measurement Method"]
        return {"columns": cols, "rows": [list(r) for r in data], "caption": "Success metrics"}

    if category == "meeting_meta":
        cols = ["Field", "Details"]
        rows = [
            ["Date", date.today().strftime("%A, %d %B %Y")],
            ["Time", "10:00 – 11:00 (60 min)"],
            ["Location", "Video conference / Meeting Room B"],
            ["Facilitator", "Project Lead"],
            ["Minutes taken by", "Autonomous Document Agent"],
        ]
        return {"columns": cols, "rows": rows, "caption": ""}

    if category == "components":
        data = [
            ("API Gateway", "Routing, authentication, rate limiting"),
            ("Application Service", "Core business logic and orchestration"),
            ("Data Store", "Persistent storage for domain entities"),
            ("Cache Layer", "Low-latency reads for hot data"),
            ("Background Workers", "Async processing and scheduled jobs"),
        ]
        cols = ["Component", "Responsibility"]
        return {"columns": cols, "rows": [list(r) for r in data], "caption": "Major components"}

    if category == "api":
        data = [
            ("POST", "/resources", "Create a new resource", "201 Created"),
            ("GET", "/resources/{id}", "Fetch a resource by id", "200 OK"),
            ("PUT", "/resources/{id}", "Update an existing resource", "200 OK"),
            ("DELETE", "/resources/{id}", "Remove a resource", "204 No Content"),
        ]
        cols = ["Method", "Path", "Description", "Success"]
        return {"columns": cols, "rows": [list(r) for r in data], "caption": "Primary API endpoints (illustrative)"}

    if category == "data_model":
        data = [
            ("User", "id, name, email, created_at", "One user has many Requests"),
            ("Request", "id, user_id, payload, status", "Belongs to a User"),
            ("Document", "id, request_id, path, size", "Belongs to a Request"),
        ]
        cols = ["Entity", "Key Fields", "Relationships"]
        return {"columns": cols, "rows": [list(r) for r in data], "caption": "Core data entities"}

    if category == "requirements":
        data = [
            ("R1", "Must", "Core capability required for launch"),
            ("R2", "Must", "Authentication and access control"),
            ("R3", "Should", "Reporting and export"),
            ("R4", "Could", "Third-party integrations"),
        ]
        cols = ["ID", "Priority (MoSCoW)", "Requirement"]
        rows = [[i, p, f"{d} for {subject}"] for i, p, d in data]
        return {"columns": cols, "rows": rows, "caption": "Prioritised requirements"}

    if category == "revision":
        cols = ["Version", "Date", "Author", "Summary of Change"]
        rows = [
            ["0.1", _future(-14), "Autonomous Document Agent", "Initial draft"],
            ["0.2", _future(-3), "Reviewer", "Incorporated review feedback"],
            ["1.0", date.today().strftime("%d %b %Y"), "Autonomous Document Agent", "Approved baseline"],
        ]
        return {"columns": cols, "rows": rows, "caption": ""}

    # generic fallback: a two-column summary
    cols = ["Item", "Detail"]
    rows = [[f"{heading} item {i}", f"Details relating to {subject}."] for i in range(1, 4)]
    return {"columns": cols, "rows": rows, "caption": ""}

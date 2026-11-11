"""Workflow tools: unit-test planning, ticket backfill, documentation.

Registered on the shared server object; see server.py for the core tools and tiers."""

from __future__ import annotations

import json

from . import profile as _profile
from . import server as _s
from . import testing as _testing
from . import tickets as _tickets
from .config import load_config
from .docs import DocEngine
from .server import (
    ToolError,
    _done,
    ix,
    mcp,
)

# ----------------------------------------------------------------------------- unit-test planning

@mcp.tool()
def test_surface(target: str, limit: int = 40) -> str:
    """Unit-test analysis of a module/folder/file: per function branches, side effects (db, network, time...),
    existing test references, real coverage if a report exists, ranked by testing priority."""
    i = ix()
    try:
        data = _testing.test_surface(i, target, limit)
    except ValueError as e:
        raise ToolError(str(e)) from e
    text = json.dumps(data, separators=(",", ":"))
    files = {it["ref"].split("::")[0] for it in data["functions_by_priority"]}
    return _done("test_surface", text, i.file_tokens(files))


@mcp.tool()
def validate_test_plan(module: str) -> str:
    """Check specs/tests/<module>/milestones.json against the schema and the index (targets must exist)."""
    try:
        plan, path = _testing.load_plan(_s.ROOT, module, load_config(_s.ROOT)["specs_dir"])
    except FileNotFoundError as e:
        raise ToolError(str(e)) from e
    errs = _testing.validate_plan(plan, ix())
    return json.dumps({"valid": not errs, "problems": errs, "milestones": len(plan.get("milestones", []))})


@mcp.tool()
def next_test_milestone(module: str) -> str:
    """The next milestone to execute (in_progress first, then todo by priority) with the plan's test command.
    Returns only that milestone so the executor's context stays small."""
    try:
        plan, _ = _testing.load_plan(_s.ROOT, module, load_config(_s.ROOT)["specs_dir"])
    except FileNotFoundError as e:
        raise ToolError(str(e)) from e
    m = _testing.next_milestone(plan)
    if m is None:
        return json.dumps({"done": True, "message": "All milestones are done or blocked."})
    return json.dumps({"test_framework": plan["test_framework"], "test_command": plan["test_command"],
                       "conventions": plan.get("conventions", ""), "milestone": m}, separators=(",", ":"))


@mcp.tool()
def update_test_milestone(module: str, milestone_id: str, status: str, notes: str = "", tests_added: int = 0) -> str:
    """Set a milestone's status (in_progress | done | blocked). 'done' is only accepted if the milestone's
    tests pass when this tool runs them; 'blocked' requires notes for the planner."""
    try:
        res = _testing.update_milestone(_s.ROOT, module, milestone_id, status, notes, tests_added,
                                        load_config(_s.ROOT)["specs_dir"])
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e)) from e
    return json.dumps(res)


# ----------------------------------------------------------------------------- ticket backfill

@mcp.tool()
def ticket_keys(limit: int = 50) -> str:
    """Ticket keys found in git history (PROJ-123, AB#123, #123) with commit counts and date range."""
    keys = _tickets.keys_in_history(_s.ROOT)[:limit]
    have = _tickets.existing_feature_keys(_s.ROOT, load_config(_s.ROOT)["specs_dir"])
    return json.dumps([{**k, "has_spec": k["key"] in have} for k in keys], separators=(",", ":"))


@mcp.tool()
def ticket_trace(key: str) -> str:
    """Commits mentioning a ticket key, files they changed, and current symbols they most likely touched
    (as path::Qualname refs for the spec's Code map)."""
    return json.dumps(_tickets.trace(_s.ROOT, ix(), key), separators=(",", ":"))


@mcp.tool()
def backfill_progress(done: list[str] | None = None, failed: dict[str, str] | None = None,
                      skipped: list[str] | None = None, query: str | None = None, reset: bool = False) -> str:
    """Read or update resumable backfill state. Call with no arguments to read; pass keys as you finish them."""
    if not any([done, failed, skipped, query, reset]):
        st = _tickets.load_state(_s.ROOT)
    else:
        st = _tickets.update_state(_s.ROOT, query, done, failed, skipped, reset)
    have = sorted(_tickets.existing_feature_keys(_s.ROOT, load_config(_s.ROOT)["specs_dir"]))
    return json.dumps({**st, "specs_existing": have}, separators=(",", ":"))


# ----------------------------------------------------------------------------- documentation

def _docs() -> DocEngine:
    return DocEngine(ix())


@mcp.tool()
def doc_plan() -> str:
    """Detected repo archetypes (backend, frontend, qa-automation, azure-functions, aws-lambda, infrastructure,
    library, cli, data-pipeline, mobile, worker, microservices) and every required document with its status."""
    de = _docs()
    return json.dumps({"archetypes": de.facts.archetypes(), "documents": de.status()}, separators=(",", ":"))


@mcp.tool()
def doc_facts(doc_id: str, max_items: int = 120) -> str:
    """Everything needed to write one document: purpose, required sections, audience, locally extracted facts
    with file:line, and must_mention (items that must appear in the document)."""
    de = _docs()
    try:
        data = de.facts_for(doc_id, max_items)
    except KeyError as e:
        raise ToolError(str(e)) from e
    text = json.dumps(data, separators=(",", ":"))
    files = set()
    for v in data["facts"].values():
        for item in (v if isinstance(v, list) else []):
            if isinstance(item, dict):
                at = str(item.get("at") or item.get("file") or "").split(":")[0]
                if at:
                    files.add(at)
    return _done("doc_facts", text, ix().file_tokens(files))


@mcp.tool()
def doc_check(doc_id: str) -> str:
    """Completeness check for one document: required sections, every must_mention item present, no placeholders
    outside 'Open questions', relative links resolve. Fix all problems, then call doc_register."""
    try:
        return json.dumps(_docs().check(doc_id))
    except KeyError as e:
        raise ToolError(str(e)) from e


@mcp.tool()
def doc_register(doc_id: str, model: str = "") -> str:
    """Record that a document passed doc_check, with a hash of the facts it describes (it turns 'stale'
    automatically when those facts change). Refuses if the check fails."""
    try:
        res = _docs().register(doc_id, model)
    except KeyError as e:
        raise ToolError(str(e)) from e
    if not res.get("registered"):
        raise ToolError("Not registered; doc_check failed: " + "; ".join(res.get("problems", [])))
    return json.dumps(res)


@mcp.tool()
def get_profile() -> str:
    """The user's SpecAnvil profile: model per agent role and ticket backfill preferences."""
    return json.dumps(_profile.effective(_s.ROOT), separators=(",", ":"))

"""SpecForge command line."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from . import __version__


def _root(args) -> Path:
    return Path(getattr(args, "path", None) or ".").resolve()


def cmd_init(args) -> int:
    from .bootstrap import run
    res = run(_root(args), dry_run=args.dry_run, force=args.force, tickets=args.tickets,
              databases=[d.strip() for d in args.db.split(",")] if args.db else None, app_url=args.app_url, ci=args.ci,
              offline=args.offline, agents=args.agents)
    if args.json:
        print(json.dumps({k: v for k, v in res.items() if k != "config"}, indent=2, default=str))
        return 0
    s = res["stack"]
    print(f"SpecForge {__version__} · {'DRY RUN · ' if res['dry_run'] else ''}{res['root']}\n")
    idx = res["index"]
    if "files_total" in idx:
        print(f"  index      {idx['files_total']} files, {idx['symbols_total']} symbols "
              f"({idx['tree_sitter_files']} via tree-sitter) in {idx['seconds']}s")
    g = res.get("grammars") or {}
    if g.get("downloaded"):
        print(f"  grammars   downloaded once: {', '.join(g['downloaded'])} (cached at {g.get('cache')})")
    elif g.get("skipped"):
        print("  grammars   offline mode: no downloads; uncached languages use the regex extractor")
    elif g:
        print("  grammars   all needed grammars already cached locally (no download)"
              + (f"; unavailable, using regex: {', '.join(g['unavailable'])}" if g.get("unavailable") else ""))
    print("  languages  " + (", ".join(l["lang"] for l in s["languages"][:8]) or "—"))
    for label, key in [("frontend", "frontend"), ("backend", "backend"), ("databases", "databases"),
                       ("tests", "test_frameworks"), ("tickets", "ticket_systems")]:
        print(f"  {label:<10} " + (", ".join(s[key]) or "—"))
    print("  connectors " + ", ".join(res["connectors"]))
    from .providers import LABELS
    print("  AI tools   " + ", ".join(LABELS[t] for t in res["agents"]))
    print()
    counts: dict[str, int] = {}
    for action, _ in res["actions"]:
        counts[action] = counts.get(action, 0) + 1
    if args.verbose or res["dry_run"]:
        for action, path in res["actions"]:
            print(f"  {action:<7} {path}")
        print()
    print("  files: " + ", ".join(f"{n} {a}" for a, n in counts.items()))
    if res["env_needed"]:
        print("\n  Set these environment variables for the connectors (never commit them):")
        for name, envs in res["env_needed"].items():
            print(f"    {name:<14} {' '.join(envs)}")
    if res.get("agent_notes"):
        print("\n  One-time steps in your AI tools:")
        for n in res["agent_notes"]:
            print(f"    - {n}")
    if not res["dry_run"]:
        from .providers import CMD_SYNTAX
        first = res["agents"][0]
        how = CMD_SYNTAX[first].format(c="groom")
        how = f"run {how} <ticket>" if how.startswith(("/", "#")) else f"ask it to use {how} for a ticket"
        print(f"\n  Next: open {LABELS[first]} here and {how}.")
        print("  Report: .specforge/report.html")
    return 0


def cmd_agents(args) -> int:
    """List detected / configured AI tools, or set them up: specforge agents [list|add <tools>|all]."""
    from . import providers as P
    from .config import load_config
    root = _root(args)
    if args.action == "list":
        cfg = load_config(root)
        det = P.detect(root)
        for t in P.ALL:
            mark = "configured" if t in (cfg.get("agents") or []) else "detected" if t in det else ""
            print(f"  {t:<9} {P.LABELS[t]:<26} {mark}")
        return 0
    from .bootstrap import run
    cfg = load_config(root)
    want = "all" if args.action == "all" else ",".join(dict.fromkeys((cfg.get("agents") or []) + (args.tools or "").split(",")))
    res = run(root, agents=want.strip(","), offline=True)
    print("Set up: " + ", ".join(P.LABELS[t] for t in res["agents"]))
    for n in res["agent_notes"]:
        print(f"  - {n}")
    return 0


def cmd_tool(args) -> int:
    """CLI bridge to every MCP tool, for agents that can't run local MCP servers (Replit) and for scripts."""
    import os
    os.environ.setdefault("SPECFORGE_ROOT", str(_root(args)))
    os.environ["SPECFORGE_TOOLS"] = "full"
    from . import server
    if args.list or not args.name:
        for name in sorted(server._FUNCS):
            doc = (server._FUNCS[name].__doc__ or "").strip().splitlines()
            print(f"  {name:<24} {doc[0][:90] if doc else ''}")
        return 0
    try:
        arguments = json.loads(args.arguments) if args.arguments else {}
    except json.JSONDecodeError as e:
        print(f"Arguments must be a JSON object: {e}", file=sys.stderr)
        return 2
    fn = server._FUNCS.get(args.name)
    if fn is None:
        import difflib
        print(f"Unknown tool '{args.name}'. Close: {difflib.get_close_matches(args.name, list(server._FUNCS), 5)}", file=sys.stderr)
        return 2
    try:
        print(fn(**arguments))
    except Exception as e:  # noqa: BLE001 - ToolError and argument errors are user-facing
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_grammars(args) -> int:
    from .index import CodeIndex
    from .parser import fetch_grammars, local_grammars
    ix = CodeIndex(_root(args))
    if not ix.is_built():
        ix.build()
    langs = args.langs or ix.code_languages()
    if args.list:
        local = local_grammars(refresh=True)
        for l in langs:
            print(f"  {'✔' if l in local else '·'} {l}")
        return 0
    res = fetch_grammars(langs)
    print(json.dumps(res, indent=2))
    if res["downloaded"]:
        print(json.dumps(ix.reparse_languages(res["downloaded"])))
    return 0


def cmd_index(args) -> int:
    from .index import CodeIndex
    print(json.dumps(CodeIndex(_root(args)).build(full=args.full), indent=2))
    return 0


def cmd_detect(args) -> int:
    from .detect import analyze_conventions, detect_stack
    from .index import CodeIndex
    ix = CodeIndex(_root(args))
    if not ix.is_built():
        ix.build()
    print(json.dumps({"stack": detect_stack(_root(args), ix), "conventions": analyze_conventions(ix)}, indent=2))
    return 0


def cmd_stats(args) -> int:
    from .index import CodeIndex
    ix = CodeIndex(_root(args))
    st, sv = ix.stats(), ix.savings()
    print(f"Index: {st['files']} files, {st['symbols']} symbols, ~{st['repo_tokens']:,} repo tokens")
    print(f"Savings: {sv['saved_pct']}% ({sv['saved_tokens']:,} tokens saved over {sv['calls']} MCP calls)")
    for t in sv["by_tool"]:
        print(f"  {t['tool']:<18} {t['calls']:>4} calls  {t['returned_tokens']:>8,} vs {t['baseline_tokens']:>9,}  "
              f"saved {t['saved_pct']}%")
    return 0


def cmd_connect(args) -> int:
    from .config import load_config, save_config
    from .connectors import catalog, env_requirements, write_mcp_json
    root = _root(args)
    cat = catalog()
    if not args.names:
        for n, c in cat.items():
            print(f"  {n:<17} [{c['category']}] {c['description']}")
        return 0
    unknown = [n for n in args.names if n not in cat]
    if unknown:
        print(f"Unknown connector(s): {', '.join(unknown)}. Run `specforge connect` to list.", file=sys.stderr)
        return 1
    cfg = load_config(root)
    current = list(cfg.get("connectors") or [])
    if args.remove:
        write_mcp_json(root, [], remove=args.names)
        cfg["connectors"] = [c for c in current if c not in args.names]
        print("Removed: " + ", ".join(args.names))
    else:
        _, added = write_mcp_json(root, args.names)
        cfg["connectors"] = current + [n for n in args.names if n not in current]
        print("Added to .mcp.json: " + (", ".join(added) or "(already present)"))
        for name, envs in env_requirements(args.names).items():
            print(f"  {name} needs: {' '.join(envs)}")
    save_config(root, cfg)
    return 0


def cmd_drift(args) -> int:
    from .index import CodeIndex
    from .specs import drift, lock_specs
    root = _root(args)
    ix = CodeIndex(root)
    ix.build()
    if args.lock:
        print(f"Locked {lock_specs(root, ix, args.spec)} code references as verified.")
        return 0
    d = drift(root, ix)
    print(d["summary"])
    for i in d["issues"]:
        if i["issue"] != "unverified" or args.verbose:
            print(f"  [{i['issue']}] {i['spec']}: {i['ref']} — {i['detail']}")
    return 1 if any(i["issue"] in ("missing", "changed") for i in d["issues"]) and args.strict else 0


def cmd_report(args) -> int:
    from .report import write_report
    print(write_report(_root(args)))
    return 0


READ_VERBS = re.compile(r"(^|[;&|]\s*|\s)(cat|less|more|head|tail|bat|grep|rg|awk|sed|strings|xxd|od|base64|"
                        r"cp|scp|rsync|curl|source|\.|type|Get-Content|nano|vim?|code)\s")


def _guard(payload: dict) -> int:
    """PreToolUse guardrail: deny access to secret files. Exit 2 blocks the tool call in Claude Code."""
    import os
    import shlex

    from .config import load_config
    from .scanner import is_secret
    if os.environ.get("SPECFORGE_ALLOW_SECRETS") == "1":
        return 0
    root = Path(payload.get("cwd") or ".")
    if (root / ".specforge").exists() and load_config(root).get("guard_secrets") is False:
        return 0
    tool = payload.get("tool_name", "")
    ti = payload.get("tool_input") or {}
    hit = None
    if tool in ("Read", "Edit", "Write", "MultiEdit"):
        fp = str(ti.get("file_path") or "")
        if fp and is_secret(fp.replace("\\", "/")):
            hit = fp
    elif tool == "Grep":
        for k in ("path", "glob"):
            v = str(ti.get(k) or "")
            if v and is_secret(v.replace("\\", "/")):
                hit = v
    elif tool == "Bash":
        cmd = str(ti.get("command") or "")
        if READ_VERBS.search(" " + cmd):
            try:
                tokens = shlex.split(cmd, posix=True)
            except ValueError:
                tokens = cmd.split()
            for t in tokens:
                if "/" in t or "." in t:
                    if is_secret(t.rstrip(";|&")):
                        hit = t
                        break
    if hit:
        print(f"SpecForge guard: access to secret file '{hit}' is blocked. Ask the user for the specific "
              "non-secret value you need, or have them set SPECFORGE_ALLOW_SECRETS=1 for this session.",
              file=sys.stderr)
        return 2
    return 0


def cmd_hook(args) -> int:
    """Claude Code hooks: `guard` (PreToolUse) and `post-edit` (PostToolUse re-index)."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if args.event == "guard":
        return _guard(payload)
    if args.event == "compact":
        from .session import touch_compaction_marker
        root = Path(payload.get("cwd") or ".").resolve()
        if (root / ".specforge").exists():
            touch_compaction_marker(root)
        return 0
    if args.event != "post-edit":
        return 0
    ti = payload.get("tool_input") or {}
    fp = ti.get("file_path") or ti.get("path")
    root = Path(payload.get("cwd") or ".").resolve()
    if not fp:
        return 0
    p = Path(fp)
    try:
        rel = (p if p.is_absolute() else root / p).resolve().relative_to(root).as_posix()
    except ValueError:
        return 0
    from .index import CodeIndex
    if (root / ".specforge" / "index.db").exists():
        CodeIndex(root).build(only=[rel])
    return 0


def cmd_profile(args) -> int:
    from . import profile as prof
    root = Path(args.path or ".").resolve()
    try:
        if args.action == "set":
            if len(args.rest) != 2:
                print("usage: specforge profile set <key> <value>", file=sys.stderr)
                return 1
            prof.set_value(args.rest[0], args.rest[1])
        elif args.action == "unset":
            if len(args.rest) != 1:
                print("usage: specforge profile unset <key>", file=sys.stderr)
                return 1
            prof.unset_value(args.rest[0])
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    eff = prof.effective(root)
    if args.action in ("set", "unset", "sync") and (root / ".specforge").exists():
        from .bootstrap import sync_agents
        changed = sync_agents(root)
        print(f"applied to {root.name}: " + (", ".join(Path(c).stem for c in changed) or "no agent changes"))
    print(f"profile: {prof.profile_path()}")
    print("models (agent role → model):")
    for role, agent in prof.ROLES.items():
        print(f"  {role:<14} {eff['models'].get(role, 'inherit'):<18} ({agent})")
    print("backfill: " + json.dumps(eff["backfill"]))
    return 0


def cmd_tests(args) -> int:
    from . import testing
    from .config import load_config
    from .index import CodeIndex
    root = Path(args.path or ".").resolve()
    ix = CodeIndex(root)
    ix.build()
    sd = load_config(root)["specs_dir"]
    if args.action == "surface":
        data = testing.test_surface(ix, args.module, 25)
        print(f"{data['functions']} functions, {data['untested_functions']} untested, coverage: "
              f"{data['avg_coverage_pct'] if data['avg_coverage_pct'] is not None else 'n/a'} ({data['coverage_source']})")
        print(f"side effects: {data['side_effects_in_module']}  mocking in repo: {data['mocking_in_repo']}")
        for it in data["functions_by_priority"][:25]:
            cov = f"{it['coverage_pct']}%" if it["coverage_pct"] is not None else "-"
            print(f"  {it['priority']:>6}  br={it['branches']:<3} refs={it['test_refs']:<3} cov={cov:<6} "
                  f"{','.join(it['side_effects']) or '-':<24} {it['ref']}")
        return 0
    try:
        plan, path = testing.load_plan(root, args.module, sd)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1
    if args.action == "validate":
        errs = testing.validate_plan(plan, ix)
        print("valid" if not errs else "\n".join(f"  ✘ {e}" for e in errs))
        return 1 if errs else 0
    for m in plan["milestones"]:
        res = m.get("result") or {}
        print(f"  {m['id']:<5} {m['status']:<12} {m['complexity']}  {m['title'][:60]}"
              + (f"  ({res.get('notes', '')[:60]})" if m["status"] == "blocked" else ""))
    done = sum(m["status"] == "done" for m in plan["milestones"])
    print(f"{done}/{len(plan['milestones'])} done · {path.relative_to(root)}")
    return 0


def cmd_docs(args) -> int:
    from .docs import DocEngine
    from .index import CodeIndex
    root = Path(args.path or ".").resolve()
    ix = CodeIndex(root)
    ix.build()
    de = DocEngine(ix)
    if args.action == "facts":
        if not args.ids:
            print("usage: specforge docs facts <doc-id>", file=sys.stderr)
            return 1
        print(json.dumps(de.facts_for(args.ids[0]), indent=1))
        return 0
    if args.action == "plan":
        for a in de.facts.archetypes():
            print(f"  {a['archetype']:<16} {a['evidence']}")
        print()
    rows = de.status()
    if args.ids:
        rows = [r for r in rows if r["id"] in args.ids]
    if args.action in ("plan", "status"):
        icon = {"fresh": "✔", "stale": "↻", "unverified": "?", "missing": "✘"}
        for r in rows:
            print(f"  {icon[r['status']]} {r['status']:<10} {r['id']:<22} {r['path']}")
        counts = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        print("  " + ", ".join(f"{n} {k}" for k, n in counts.items()))
        return 0
    # check
    failed = 0
    for r in rows:
        if r["status"] == "missing" and not args.strict:
            continue
        res = de.check(r["id"])
        stale = r["status"] == "stale"
        if res["ok"] and not stale:
            print(f"  ✔ {r['id']}")
            continue
        failed += 1
        print(f"  ✘ {r['id']} ({r['path']})" + ("  [stale: code changed since written]" if stale else ""))
        for p in res["problems"][:8]:
            print(f"      - {p}")
    print(f"{failed} document(s) need work" if failed else "all documents complete and current")
    return 1 if (failed and args.strict) else 0


def cmd_embed(args) -> int:
    from . import semantic
    from .index import CodeIndex
    ix = CodeIndex(_root(args))
    ix.build()
    if args.code_model:
        from .config import load_config, save_config
        cfg = load_config(ix.root)
        cfg["semantic"] = {**(cfg.get("semantic") or {}), "model": semantic.CODE_MODEL}
        save_config(ix.root, cfg)
    try:
        res = semantic.build(ix, progress=lambda d, t: print(f"\r  embedded {d}/{t}", end="", file=sys.stderr))
    except ImportError:
        print("Semantic search needs the optional extra: pip install 'specforge[semantic]'", file=sys.stderr)
        return 1
    print(file=sys.stderr)
    print(json.dumps(res, indent=2))
    return 0


def cmd_source(args) -> int:
    from . import sources
    if args.action == "list":
        for k, v in sources.registry().items():
            print(f"  {k:<32} {v['version']:<12} {v['files']:>6} files {v['symbols']:>7} symbols  {v['root']}")
        return 0
    if not args.spec:
        print("usage: specforge source add|remove <pkg:name | gh:owner/repo[@ref] | path:dir>", file=sys.stderr)
        return 1
    if args.action == "remove":
        print("removed" if sources.remove(args.spec) else "not found")
        return 0
    try:
        print(json.dumps(sources.add(args.spec, Path(args.path or ".").resolve(), allow_network=True), indent=2))
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_watch(args) -> int:
    """Keep the index fresh while you (or any tool) edit files. Polling: ~0.1 s per pass on a 5k-file repo."""
    import time as _time

    from .index import CodeIndex
    ix = CodeIndex(_root(args))
    print(f"watching {ix.root} every {args.interval}s (Ctrl-C to stop)")
    sem = None
    if args.embed:
        from . import semantic as sem
    try:
        while True:
            r = ix.build()
            if r["files_changed"] or r["files_removed"]:
                msg = f"{_time.strftime('%H:%M:%S')} re-indexed {r['files_changed']} changed, {r['files_removed']} removed"
                if sem is not None and sem.has_vectors(ix):
                    msg += f", embedded {sem.build(ix)['embedded']}"
                print(msg, flush=True)
            _time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


def cmd_analyze(args) -> int:
    from . import astsearch, graph
    from .analysis import hotspots
    from .index import CodeIndex
    ix = CodeIndex(_root(args))
    ix.build()
    what = args.what
    if what == "importance":
        data = graph.importance(ix, args.limit)
    elif what == "dead":
        data = graph.dead_code(ix, args.limit)
    elif what == "cycles":
        data = {"file": graph.cycles(ix, "file"), "module": graph.cycles(ix, "module")}
    elif what == "coupling":
        data = graph.coupling(ix, args.limit)
    elif what == "layers":
        data = graph.layer_violations(ix)
    elif what == "risk":
        data = graph.pr_risk(ix, args.base)
    elif what == "hotspots":
        data = hotspots(ix, limit=args.limit)
    elif what == "ast":
        data = astsearch.search(ix, preset=args.preset or "broad-except", limit=args.limit) if args.preset \
            else astsearch.presets()
    else:
        data = {}
    print(json.dumps(data, indent=1))
    return 0


def cmd_pack(args) -> int:
    from . import packs
    try:
        if args.action == "list":
            for p in packs.list_catalog():
                print(f"  {'✔' if p['installed'] else '·'} {p['pack']:<26} {p['source']:<40} {p['about']}")
            return 0
        if not args.spec:
            print("usage: specforge pack add|export|import <spec|source|file> [-o out.sfpack]", file=sys.stderr)
            return 1
        if args.action == "add":
            res = packs.add(args.spec)
        elif args.action == "export":
            res = packs.export(args.spec, Path(args.out or (re.sub(r"[^\w.@-]+", "_", args.spec) + ".sfpack")))
        else:
            res = packs.import_pack(Path(args.spec))
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2))
    return 0


def cmd_insight(args) -> int:
    from . import agentaudit, history, insights, regret, runtime
    from .index import CodeIndex
    ix = CodeIndex(_root(args))
    ix.build()
    w, t = args.what, args.target
    fn = {
        "health": lambda: insights.repo_health(ix), "untested": lambda: insights.untested_symbols(ix, t, args.limit),
        "similar": lambda: insights.similar_symbols(ix, t, args.threshold, 6, args.limit),
        "complexity": lambda: insights.symbol_complexity(ix, t, args.limit) if t else insights.symbol_complexity_all(ix, 10, args.limit),
        "extract": lambda: insights.extraction_candidates(ix, args.limit), "entry": lambda: insights.entry_points(ix, args.limit),
        "chains": lambda: insights.signal_chains(ix, t, 6, args.limit), "tectonic": lambda: insights.tectonic_map(ix),
        "architecture": lambda: insights.architecture_metrics(ix), "decorators": lambda: insights.decorator_census(ix, args.limit),
        "flow": lambda: insights.flow_edges(ix), "columns": lambda: insights.search_columns(ix, t or "", args.limit),
        "safety": lambda: insights.check_safety(ix, t, args.action or "edit", args.new_name),
        "endpoint": lambda: insights.endpoint_impact(ix, t), "risk-file": lambda: insights.file_risk(ix, t),
        "provenance": lambda: history.provenance(ix, t), "diff": lambda: history.symbol_diff(ix, t or "HEAD"),
        "digest": lambda: history.digest(ix, t or "1 day ago"), "delivery": lambda: history.delivery_metrics(ix, args.days),
        "churn": lambda: history.churn_rate(ix, t, args.days), "audit": lambda: agentaudit.audit(ix.root),
        "trace-import": lambda: runtime.import_trace(ix, t), "hot": lambda: runtime.hot_paths(ix, args.limit, t),
        "unused": lambda: runtime.unused_paths(ix, args.days, args.limit),
        "corrections": lambda: regret.suggest_corrections(ix, args.days, args.apply),
        "tune": lambda: regret.tune_weights(ix, t, args.days, args.apply),
        "contracts": lambda: insights.group_contracts(ix, None, args.limit),
    }.get(w)
    if fn is None:
        print("unknown insight", file=sys.stderr)
        return 1
    try:
        print(json.dumps(fn(), indent=1, default=str))
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_diagram(args) -> int:
    from . import diagrams
    from .index import CodeIndex
    ix = CodeIndex(_root(args))
    ix.build()
    mm = diagrams.render(ix, args.kind, args.target or "", args.depth)
    if args.out:
        out = Path(args.out)
        out.write_text(diagrams.to_html(mm, f"{args.kind} {args.target or ''}") if out.suffix == ".html"
                       else f"```mermaid\n{mm}\n```\n", encoding="utf-8")
        print(out)
    else:
        print(mm)
    return 0


def cmd_doctor(args) -> int:
    root = _root(args)
    checks = []
    try:
        import mcp  # noqa: F401
        checks.append(("mcp SDK", True, ""))
    except ImportError:
        checks.append(("mcp SDK", False, "pip install mcp"))
    try:
        import tree_sitter_language_pack as t
        t.get_parser("python")
        checks.append(("tree-sitter grammars", True, "downloaded on demand and cached"))
    except Exception as e:  # noqa: BLE001
        checks.append(("tree-sitter grammars", False, f"regex fallback will be used ({e.__class__.__name__})"))
    checks.append(("specforge-mcp on PATH", bool(shutil.which("specforge-mcp")),
                   "install with `uv tool install` or `pipx install` so Claude Code can start it"))
    checks.append(("npx (Playwright/other MCPs)", bool(shutil.which("npx")), "install Node.js 18+"))
    checks.append(("uvx (Python MCPs)", bool(shutil.which("uvx")), "install uv"))
    checks.append((".mcp.json", (root / ".mcp.json").exists(), "run `specforge init`"))
    checks.append(("index", (root / ".specforge" / "index.db").exists(), "run `specforge index`"))
    for name, ok, hint in checks:
        print(f"  {'✔' if ok else '✘'} {name}" + ("" if ok else f"  → {hint}"))
    return 0


def main(argv: list[str] | None = None) -> int:
    import signal
    if hasattr(signal, "SIGPIPE"):  # `specforge ... | head` should exit quietly
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    ap = argparse.ArgumentParser(prog="specforge", description="Spec-driven development for any repo.")
    ap.add_argument("--version", action="version", version=f"specforge {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="bootstrap specs, skills, agents, commands, hooks and MCP config")
    p.add_argument("path", nargs="?")
    p.add_argument("--dry-run", action="store_true", help="show what would be written")
    p.add_argument("--force", action="store_true", help="overwrite generated specs and skills")
    p.add_argument("--tickets", help="jira | azure-devops | github | linear | gitlab")
    p.add_argument("--db", help="comma list: postgres,mysql,sqlserver,mongodb,sqlite")
    p.add_argument("--app-url", help="URL of the running app for Playwright")
    p.add_argument("--ci", action="store_true", help="also add a GitHub Actions spec-drift workflow")
    p.add_argument("--offline", action="store_true", help="never download parser grammars (regex fallback)")
    p.add_argument("--agents", help="AI tools to set up: auto (default: detected), all, or a comma list of "
                                    "claude,copilot,cursor,augment,windsurf,codex,kiro,gemini,replit")
    p.add_argument("--json", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("agents", help="AI coding tools: list | add <tools> | all")
    p.add_argument("action", choices=["list", "add", "all"], nargs="?", default="list")
    p.add_argument("tools", nargs="?", help="comma list for add, e.g. cursor,copilot")
    p.add_argument("--path")
    p.set_defaults(fn=cmd_agents)

    p = sub.add_parser("tool", help="call any MCP tool from the shell: tool <name> '<json args>' | --list")
    p.add_argument("name", nargs="?")
    p.add_argument("arguments", nargs="?", help='JSON object, e.g. \'{"query": "create user"}\'')
    p.add_argument("--list", action="store_true")
    p.add_argument("--path")
    p.set_defaults(fn=cmd_tool)

    p = sub.add_parser("index", help="build or refresh the code index")
    p.add_argument("path", nargs="?")
    p.add_argument("--full", action="store_true")
    p.set_defaults(fn=cmd_index)

    p = sub.add_parser("grammars", help="explicitly download tree-sitter grammars (the only network step)")
    p.add_argument("langs", nargs="*")
    p.add_argument("--path")
    p.add_argument("--list", action="store_true", help="show which grammars are cached locally")
    p.set_defaults(fn=cmd_grammars)

    p = sub.add_parser("detect", help="print detected stack and conventions as JSON")
    p.add_argument("path", nargs="?")
    p.set_defaults(fn=cmd_detect)

    p = sub.add_parser("stats", help="index size and token savings")
    p.add_argument("path", nargs="?")
    p.set_defaults(fn=cmd_stats)

    p = sub.add_parser("connect", help="list or add/remove MCP connectors in .mcp.json")
    p.add_argument("names", nargs="*")
    p.add_argument("--remove", action="store_true")
    p.add_argument("--path")
    p.set_defaults(fn=cmd_connect)

    p = sub.add_parser("drift", help="check specs against code")
    p.add_argument("path", nargs="?")
    p.add_argument("--lock", action="store_true", help="record current code as verified")
    p.add_argument("--spec", help="limit --lock to one spec file")
    p.add_argument("--strict", action="store_true", help="exit 1 on changed/missing refs (for CI)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(fn=cmd_drift)

    p = sub.add_parser("report", help="write .specforge/report.html")
    p.add_argument("path", nargs="?")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("serve", help="run the MCP server on stdio")
    p.set_defaults(fn=lambda a: __import__("specforge.server", fromlist=["main"]).main() or 0)

    p = sub.add_parser("profile", help="show or change your personal profile (models per role, backfill)")
    p.add_argument("action", nargs="?", default="show", choices=["show", "set", "unset", "sync"])
    p.add_argument("rest", nargs="*")
    p.add_argument("--path")
    p.set_defaults(fn=cmd_profile)

    p = sub.add_parser("tests", help="unit-test planning: surface | validate | status <module>")
    p.add_argument("action", choices=["surface", "validate", "status"])
    p.add_argument("module")
    p.add_argument("--path")
    p.set_defaults(fn=cmd_tests)

    p = sub.add_parser("docs", help="documentation: plan | status | check | facts <id>")
    p.add_argument("action", choices=["plan", "status", "check", "facts"])
    p.add_argument("ids", nargs="*")
    p.add_argument("--strict", action="store_true", help="check: also fail on missing/stale docs (for CI)")
    p.add_argument("--path")
    p.set_defaults(fn=cmd_docs)

    p = sub.add_parser("embed", help="build local embeddings for semantic search (one-time model download)")
    p.add_argument("path", nargs="?")
    p.add_argument("--code-model", action="store_true", help="use a code-trained model (larger download)")
    p.set_defaults(fn=cmd_embed)

    p = sub.add_parser("source", help="index dependencies / external repos: add|list|remove <spec>")
    p.add_argument("action", choices=["add", "list", "remove"])
    p.add_argument("spec", nargs="?")
    p.add_argument("--path")
    p.set_defaults(fn=cmd_source)

    p = sub.add_parser("watch", help="keep the index (and embeddings) fresh while files change")
    p.add_argument("path", nargs="?")
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--embed", action="store_true", help="also refresh embeddings for changed symbols")
    p.set_defaults(fn=cmd_watch)

    p = sub.add_parser("analyze", help="importance | dead | cycles | coupling | layers | risk | hotspots | ast")
    p.add_argument("what", choices=["importance", "dead", "cycles", "coupling", "layers", "risk", "hotspots", "ast"])
    p.add_argument("--path")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--base", default="HEAD")
    p.add_argument("--preset")
    p.set_defaults(fn=cmd_analyze)

    p = sub.add_parser("pack", help="starter packs without installing: list | add <name|npm:|pypi:|go:|gh:> | export | import")
    p.add_argument("action", choices=["list", "add", "export", "import"])
    p.add_argument("spec", nargs="?")
    p.add_argument("-o", "--out")
    p.set_defaults(fn=cmd_pack)

    p = sub.add_parser("insight", help="health | untested | similar | complexity | extract | entry | chains | tectonic | "
                                       "architecture | decorators | flow | columns | safety | endpoint | risk-file | "
                                       "provenance | diff | digest | delivery | churn | audit | trace-import <file> | "
                                       "hot | unused | corrections [--apply] | tune [eval.json] [--apply] | contracts")
    p.add_argument("what")
    p.add_argument("target", nargs="?")
    p.add_argument("--path")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--threshold", type=float, default=0.8)
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--action", choices=["edit", "delete", "rename"])
    p.add_argument("--new-name")
    p.add_argument("--apply", action="store_true", help="corrections / tune: save the result to .specforge/config.json")
    p.set_defaults(fn=cmd_insight)

    p = sub.add_parser("diagram", help="Mermaid: call | class | deps | modules | sequence | tectonic  (-o x.html / x.md)")
    p.add_argument("kind")
    p.add_argument("target", nargs="?")
    p.add_argument("--path")
    p.add_argument("--depth", type=int, default=2)
    p.add_argument("-o", "--out")
    p.set_defaults(fn=cmd_diagram)

    p = sub.add_parser("hook", help="internal: Claude Code hook handler")
    p.add_argument("event")
    p.set_defaults(fn=cmd_hook)

    p = sub.add_parser("doctor", help="check the local setup")
    p.add_argument("path", nargs="?")
    p.set_defaults(fn=cmd_doctor)

    args = ap.parse_args(argv)
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())

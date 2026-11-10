"""SpecForge command line."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import __version__


def _root(args) -> Path:
    return Path(getattr(args, "path", None) or ".").resolve()


def cmd_init(args) -> int:
    from .bootstrap import run
    res = run(_root(args), dry_run=args.dry_run, force=args.force, tickets=args.tickets,
              databases=[d.strip() for d in args.db.split(",")] if args.db else None, app_url=args.app_url, ci=args.ci)
    if args.json:
        print(json.dumps({k: v for k, v in res.items() if k != "config"}, indent=2, default=str))
        return 0
    s = res["stack"]
    print(f"SpecForge {__version__} · {'DRY RUN · ' if res['dry_run'] else ''}{res['root']}\n")
    idx = res["index"]
    if "files_total" in idx:
        print(f"  index      {idx['files_total']} files, {idx['symbols_total']} symbols "
              f"({idx['tree_sitter_files']} via tree-sitter) in {idx['seconds']}s")
    print("  languages  " + (", ".join(l["lang"] for l in s["languages"][:8]) or "—"))
    for label, key in [("frontend", "frontend"), ("backend", "backend"), ("databases", "databases"),
                       ("tests", "test_frameworks"), ("tickets", "ticket_systems")]:
        print(f"  {label:<10} " + (", ".join(s[key]) or "—"))
    print("  connectors " + ", ".join(res["connectors"]))
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
    if not res["dry_run"]:
        print("\n  Next: open Claude Code here, then try `/sf:learn` and `/sf:groom <ticket>`.")
        print("  Report: .specforge/report.html")
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


def cmd_hook(args) -> int:
    """Claude Code hook: re-index files right after Claude edits them."""
    if args.event != "post-edit":
        return 0
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
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
    p.add_argument("--json", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("index", help="build or refresh the code index")
    p.add_argument("path", nargs="?")
    p.add_argument("--full", action="store_true")
    p.set_defaults(fn=cmd_index)

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

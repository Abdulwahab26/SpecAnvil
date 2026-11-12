"""Every MCP tool and every CLI command runs."""



from helpers import (
    _openspec_repo,
    _otlp,
    _server_on,
)


def test_every_mcp_tool_runs_without_crashing(tmp_path, monkeypatch):
    """Call all tools in-process with plausible arguments. A ToolError (a clear, user-facing refusal) is fine;
    any other exception is a bug."""
    import inspect

    from specanvil.bootstrap import run
    from specanvil.server import ToolError
    monkeypatch.setenv("SPECANVIL_HOME", str(tmp_path / "home"))
    root = _openspec_repo(tmp_path)
    run(root, agents="claude", offline=True)
    server = _server_on(root, monkeypatch)
    trace = tmp_path / "t.json"
    _otlp(trace, [({"code.filepath": "app/crud.py", "code.function": "create_user"}, 4.0)] * 3)
    by_name = {
        "query": "create user", "task": "add email validation to signup", "pattern": "create_user", "ref": "app/crud.py::create_user",
        "refs": ["app/crud.py::create_user"], "path": "app/crud.py", "target": "app/crud.py", "name": "User", "module": "app",
        "endpoint": "POST /users/signup", "left": "app/api.py", "right": "app/billing.py", "kind": "modules", "spec": "express",
        "title": "Signup audit", "sections": {"Summary": "Signup reviewed end to end for this audit."}, "symbol": "create_user",
        "base": "HEAD", "head": "HEAD", "since": "1 year ago", "preset": "broad-except", "action": "edit", "new_name": "make_user",
        "milestone_id": "M1", "status": "blocked", "notes": "needs a decision", "plan": {}, "doc_id": "readme", "summaries": [],
        "key": "PROJ-1", "keys": ["PROJ-1"], "tier": "", "source": None, "section": None, "eval_file": "", "members": None,
        "arguments": {}, "entry": None, "items": {}, "old": "create_user", "new": "make_user", "symbols": ["create_user"], "id": 1,
    }
    special = {"import_runtime_trace": {"path": str(trace)}, "read_spec": {"path": "openspec/specs/signup/spec.md"},
               "run_tool": {"name": "repo_map"}, "add_source": {"spec": f"path:{root}"}, "add_pack": {"spec": "definitely-not-a-pack"},
               "get_file_content": {"path": "app/crud.py"}, "file_outline": {"path": "app/crud.py"}, "file_risk": {"path": "app/crud.py"},
               "dependency_graph": {"path": "app"}, "render_diagram": {"kind": "modules"}, "search_ast": {"preset": "broad-except"}}
    crashes, ran, refused = [], 0, 0
    for name, fn in sorted(server._FUNCS.items()):
        params = inspect.signature(fn).parameters
        kwargs = dict(special.get(name, {}))
        for pname, p in params.items():
            if pname in kwargs or p.default is not inspect.Parameter.empty:
                continue
            if pname not in by_name:
                crashes.append(f"{name}: no test value for required parameter '{pname}'")
                break
            kwargs[pname] = by_name[pname]
        else:
            try:
                out = fn(**kwargs)
                assert isinstance(out, str), f"{name} returned {type(out)}"
                ran += 1
            except (ToolError, ValueError):
                refused += 1
            except Exception as e:  # noqa: BLE001
                crashes.append(f"{name}({kwargs}): {type(e).__name__}: {e}")
    assert not crashes, "\n".join(crashes)
    assert ran >= 70 and ran + refused == len(server._FUNCS), (ran, refused, len(server._FUNCS))


def test_every_cli_command_runs(tmp_path, monkeypatch, capsys):
    from specanvil.cli import main
    monkeypatch.setenv("SPECANVIL_HOME", str(tmp_path / "home"))
    root = _openspec_repo(tmp_path)
    r = str(root)
    assert main(["init", r, "--offline", "--agents", "claude,cursor"]) == 0
    ok = [["index", r], ["detect", r], ["stats", r], ["drift", r], ["report", r], ["doctor"],
          ["agents", "list", "--path", r], ["profile", "--path", r], ["docs", "plan", "--path", r], ["docs", "status", "--path", r],
          ["analyze", "importance", "--path", r], ["analyze", "cycles", "--path", r], ["analyze", "dead", "--path", r],
          ["analyze", "coupling", "--path", r], ["analyze", "hotspots", "--path", r], ["insight", "health", "--path", r],
          ["insight", "untested", "--path", r], ["insight", "entry", "--path", r], ["insight", "flow", "--path", r],
          ["insight", "audit", "--path", r], ["insight", "contracts", "--path", r], ["diagram", "modules", "--path", r],
          ["openspec", "status", "--path", r], ["openspec", "check", "--path", r], ["openspec", "coverage", "--path", r],
          ["openspec", "gaps", "--path", r], ["pack", "list"], ["source", "list", "--path", r], ["connect", "--path", r],
          ["tests", "surface", "app", "--path", r], ["grammars", "--list", "--path", r], ["uninstall", "cursor", "--dry-run", "--path", r],
          ["uninstall", "--path", r]]
    failed = []
    for argv in ok:
        try:
            rc = main(argv)
        except SystemExit as e:
            rc = e.code
        except Exception as e:  # noqa: BLE001
            rc = f"{type(e).__name__}: {e}"
        if rc not in (0, None, 1):
            failed.append((argv[:3], rc))
    capsys.readouterr()
    assert not failed, failed


def test_tool_reference_is_up_to_date():
    """docs/TOOLS.md is generated from the tool registry; regenerate with `python scripts/gen_tool_reference.py`."""
    import subprocess
    import sys
    from pathlib import Path
    script = Path(__file__).resolve().parent.parent / "scripts" / "gen_tool_reference.py"
    assert subprocess.run([sys.executable, str(script), "--check"]).returncode == 0

"""Shared fixtures for the unit tests: small synthetic repos and trace files."""
import json
import subprocess
from pathlib import Path


def make_repo(tmp: Path) -> Path:
    (tmp / "src" / "orders").mkdir(parents=True)
    (tmp / "src" / "orders" / "service.py").write_text(
        'class OrderService:\n    """Creates orders."""\n    def create(self, items):\n'
        '        if not items:\n            raise ValueError("empty")\n        return {"items": items}\n\n'
        'def total(items):\n    return sum(i["price"] for i in items)\n')
    (tmp / "src" / "orders" / "test_service.py").write_text("def test_total():\n    assert True\n")
    (tmp / "requirements.txt").write_text("fastapi\nsqlalchemy\npsycopg\npytest\n")
    (tmp / ".env").write_text("SECRET=abc123456\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    return tmp


def _plan(test_file: str) -> dict:
    return {"module": "orders", "test_framework": "pytest", "test_command": "python -m pytest {test_file} -q -p no:cacheprovider",
            "coverage_target": 80, "milestones": [{
                "id": "M1", "title": "total()", "priority": 1, "status": "todo", "complexity": "S",
                "targets": ["src/orders/service.py::total"], "test_file": test_file,
                "cases": [{"id": "M1-C1", "name": "sums prices", "kind": "happy", "given": "two items",
                           "when": "total", "then": "sum"}],
                "challenges": [{"challenge": "none", "approach": "pure function"}], "done_when": ["passes"]}]}


def _azure_repo(tmp: Path) -> Path:
    (tmp / "host.json").write_text('{"version": "2.0", "functionTimeout": "00:05:00"}')
    (tmp / "requirements.txt").write_text("azure-functions\n")
    (tmp / "local.settings.json").write_text('{"Values": {"DB_URL": "Server=x;Password=secret"}}')
    (tmp / "function_app.py").write_text(
        'import os\nimport azure.functions as func\napp = func.FunctionApp()\n\n'
        '@app.route(route="orders/{id}")\ndef get_order(req):\n    return os.environ["DB_URL"]\n\n'
        '@app.timer_trigger(schedule="0 0 2 * * *", arg_name="t")\ndef nightly(t):\n    pass\n')
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    return tmp


def _graph_repo(tmp: Path) -> Path:
    (tmp / "app").mkdir()
    (tmp / "app" / "__init__.py").write_text("")
    (tmp / "app" / "crud.py").write_text(
        'from app.models import User\n\nMAX_USERS = 100\n\n'
        'def create_user(name):\n    """Create and persist."""\n    return _save(User(name))\n\n'
        'def _save(obj):\n    return obj\n\ndef _never_used(x):\n    return x * 2\n')
    (tmp / "app" / "models.py").write_text(
        'class Base:\n    pass\n\nclass User(Base):\n    name: str\n\n    def __init__(self, name):\n'
        '        self.name = name\n\nclass Admin(User):\n    level = 1\n')
    (tmp / "app" / "routes.py").write_text(
        'from app import crud\nfrom app.models import User\n\n'
        'def create_user(payload):\n    # create_user in a comment must not be renamed\n'
        '    label = "create_user"\n    return crud.create_user(payload["name"])\n\n'
        'def items():\n    try:\n        return []\n    except Exception:\n        pass\n\n'
        'def page(x=[]):\n    return x\n')
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    return tmp


def _git_commit(root: Path, msg: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", msg], cwd=root, check=True)


def _insight_repo(tmp: Path) -> Path:
    root = _graph_repo(tmp)
    (root / "app" / "api.py").write_text(
        'from fastapi import APIRouter\nfrom app import crud\nrouter = APIRouter(prefix="/users")\n\n'
        '@router.post("/signup")\ndef signup(payload):\n    if not payload:\n        raise ValueError("x")\n'
        '    for i in range(3):\n        if i > 1:\n            pass\n    return crud.create_user(payload)\n\n'
        'def helper_a(items):\n    total = 0\n    for it in items:\n        if it.price > 10:\n            total += it.price * 2\n'
        '        else:\n            total += it.price\n    return round(total, 2)\n')
    (root / "app" / "billing.py").write_text(
        'def helper_b(items):\n    total = 0\n    for it in items:\n        if it.price > 10:\n            total += it.price * 2\n'
        '        else:\n            total += it.price\n    return round(total, 2)\n\n'
        'def notify(bus):\n    bus.emit("user.created", 1)\n\ndef listen(bus):\n    bus.on("user.created", print)\n')
    (root / "tests").mkdir()
    (root / "tests" / "test_api.py").write_text("from app.api import signup\n\ndef test_signup():\n    assert signup({'name': 'a'})\n")
    (root / "web.ts").write_text('export async function register() { return fetch("/users/signup", {method: "POST"}); }\n')
    _git_commit(root, "PROJ-1 initial because we need users")
    return root


def _otlp(path: Path, spans: list[tuple[dict, float]]) -> None:
    t, out = 1_700_000_000_000_000_000, []
    for attrs, ms in spans:
        t += 10_000_000
        out.append({"name": attrs.get("name", "s"), "startTimeUnixNano": str(t), "endTimeUnixNano": str(t + int(ms * 1e6)),
                    "attributes": [{"key": k, "value": {"stringValue": v} if isinstance(v, str) else {"intValue": v}}
                                   for k, v in attrs.items() if k != "name"]})
    path.write_text(json.dumps({"resourceSpans": [{"scopeSpans": [{"spans": out}]}]}))


def I_file_risk(ix, path):
    from specanvil import insights as I
    return I.file_risk(ix, path)["symbols"]


def _tool_repo(tmp: Path) -> Path:
    root = _graph_repo(tmp)
    (root / "package.json").write_text('{"name": "demo", "scripts": {"test": "vitest"}}')
    return root


def _openspec_repo(tmp: Path) -> Path:
    root = _insight_repo(tmp)
    cap = root / "openspec" / "specs" / "signup"
    cap.mkdir(parents=True)
    (cap / "spec.md").write_text(
        "# signup Specification\n\n## Purpose\nLet people create an account.\n\n## Requirements\n\n"
        "### Requirement: Signup creates a user\nThe system SHALL create a user from a valid payload. See `app/crud.py::create_user`.\n\n"
        "#### Scenario: Valid payload creates a user\n- **WHEN** a valid payload is posted\n- **THEN** a user is stored\n\n"
        "#### Scenario: Empty payload is rejected\n- **WHEN** the payload is empty\n- **THEN** an error is returned\n\n"
        "### Requirement: Old thing\nThe system SHALL do the old thing. See `app/crud.py::gone`.\n", encoding="utf-8")
    ch = root / "openspec" / "changes" / "add-email-check"
    (ch / "specs" / "signup").mkdir(parents=True)
    (ch / "proposal.md").write_text("## Why\nBad emails.\n\n## What Changes\nValidate email.\n", encoding="utf-8")
    (ch / "tasks.md").write_text("## 1. Do\n- [x] 1.1 Add check\n- [ ] 1.2 Add test\n", encoding="utf-8")
    (ch / "specs" / "signup" / "spec.md").write_text(
        "## ADDED Requirements\n\n### Requirement: Signup validates email\nThe system SHALL reject malformed emails.\n\n"
        "#### Scenario: Malformed email\n- **WHEN** the email has no @\n- **THEN** signup fails\n\n"
        "## MODIFIED Requirements\n\n### Requirement: Signup makes a user\nThe system SHALL create a user.\n\n"
        "#### Scenario: Still works\n- **WHEN** valid\n- **THEN** stored\n", encoding="utf-8")
    (root / "openspec" / "changes" / "archive" / "2026-01-01-old").mkdir(parents=True)
    (root / "tests" / "test_api.py").write_text(
        "def test_valid_payload_creates_a_user():\n    assert True\n\ndef test_rejects_payload_when_empty():\n    assert True\n",
        encoding="utf-8")
    _git_commit(root, "openspec")
    return root


def _server_on(root: Path, monkeypatch):
    from specanvil import server
    from specanvil.session import Session
    monkeypatch.setattr(server, "ROOT", root)
    monkeypatch.setattr(server, "_ix", None)
    monkeypatch.setattr(server, "_session", Session(root))
    return server

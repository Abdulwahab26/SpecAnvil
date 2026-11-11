"""Detect stack, frameworks, databases, ticket systems, test tools and run commands.

Everything is inferred from files in the repo; nothing leaves the machine.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from pathlib import Path

from .index import CodeIndex

# dependency/name substring -> (category, label)
DEP_SIGNALS: list[tuple[str, str, str]] = [
    # frontend
    ("react", "frontend", "React"), ("next", "frontend", "Next.js"), ("vue", "frontend", "Vue"),
    ("nuxt", "frontend", "Nuxt"), ("@angular/core", "frontend", "Angular"), ("svelte", "frontend", "Svelte"),
    ("solid-js", "frontend", "SolidJS"), ("tailwindcss", "frontend", "Tailwind CSS"),
    ("@mui/material", "frontend", "MUI"), ("antd", "frontend", "Ant Design"), ("redux", "frontend", "Redux"),
    ("@tanstack/react-query", "frontend", "TanStack Query"), ("zustand", "frontend", "Zustand"),
    ("react-native", "mobile", "React Native"), ("expo", "mobile", "Expo"), ("flutter", "mobile", "Flutter"),
    # backend
    ("express", "backend", "Express"), ("@nestjs/core", "backend", "NestJS"), ("fastify", "backend", "Fastify"),
    ("koa", "backend", "Koa"), ("hono", "backend", "Hono"), ("django", "backend", "Django"),
    ("fastapi", "backend", "FastAPI"), ("flask", "backend", "Flask"), ("spring-boot", "backend", "Spring Boot"),
    ("microsoft.aspnetcore", "backend", "ASP.NET Core"), ("gin-gonic", "backend", "Gin"),
    ("labstack/echo", "backend", "Echo"), ("gofiber", "backend", "Fiber"), ("rails", "backend", "Rails"),
    ("laravel", "backend", "Laravel"), ("symfony", "backend", "Symfony"), ("actix-web", "backend", "Actix"),
    ("axum", "backend", "Axum"), ("ktor", "backend", "Ktor"), ("graphql", "backend", "GraphQL"),
    ("@trpc/server", "backend", "tRPC"),
    # ORM / data
    ("prisma", "orm", "Prisma"), ("typeorm", "orm", "TypeORM"), ("sequelize", "orm", "Sequelize"),
    ("drizzle-orm", "orm", "Drizzle"), ("mongoose", "orm", "Mongoose"), ("sqlalchemy", "orm", "SQLAlchemy"),
    ("hibernate", "orm", "Hibernate"), ("spring-boot-starter-data-jpa", "orm", "Spring Data JPA"),
    ("entityframeworkcore", "orm", "EF Core"), ("dapper", "orm", "Dapper"), ("gorm", "orm", "GORM"),
    ("knex", "orm", "Knex"), ("sqlmodel", "orm", "SQLModel"), ("alembic", "orm", "Alembic migrations"),
    ("flyway-core", "orm", "Flyway migrations"), ("liquibase-core", "orm", "Liquibase migrations"),
    ("tortoise-orm", "orm", "Tortoise ORM"), ("peewee", "orm", "Peewee"), ("ent", "orm", "ent"), ("mikro-orm", "orm", "MikroORM"), ("diesel", "orm", "Diesel"),
    # databases
    ("pg", "database", "postgres"), ("postgres", "database", "postgres"), ("psycopg", "database", "postgres"),
    ("asyncpg", "database", "postgres"), ("npgsql", "database", "postgres"), ("postgresql", "database", "postgres"),
    ("lib/pq", "database", "postgres"), ("jackc/pgx", "database", "postgres"),
    ("mysql", "database", "mysql"), ("mysql2", "database", "mysql"), ("pymysql", "database", "mysql"),
    ("mariadb", "database", "mysql"), ("go-sql-driver/mysql", "database", "mysql"),
    ("mssql", "database", "sqlserver"), ("tedious", "database", "sqlserver"), ("pyodbc", "database", "sqlserver"),
    ("entityframeworkcore.sqlserver", "database", "sqlserver"), ("microsoft.data.sqlclient", "database", "sqlserver"),
    ("mssql-jdbc", "database", "sqlserver"), ("mongodb", "database", "mongodb"), ("mongoose", "database", "mongodb"),
    ("pymongo", "database", "mongodb"), ("motor", "database", "mongodb"), ("sqlite3", "database", "sqlite"),
    ("better-sqlite3", "database", "sqlite"), ("redis", "database", "redis"), ("ioredis", "database", "redis"),
    ("oracledb", "database", "oracle"), ("ojdbc", "database", "oracle"), ("cassandra", "database", "cassandra"),
    ("dynamodb", "database", "dynamodb"), ("elasticsearch", "database", "elasticsearch"),
    ("supabase", "database", "postgres"), ("@neondatabase", "database", "postgres"),
    # tests
    ("jest", "test", "Jest"), ("vitest", "test", "Vitest"), ("mocha", "test", "Mocha"),
    ("@playwright/test", "test", "Playwright"), ("playwright", "test", "Playwright"),
    ("cypress", "test", "Cypress"), ("@testing-library", "test", "Testing Library"),
    ("pytest", "test", "pytest"), ("junit", "test", "JUnit"), ("testng", "test", "TestNG"),
    ("xunit", "test", "xUnit"), ("nunit", "test", "NUnit"), ("mstest", "test", "MSTest"),
    ("rspec", "test", "RSpec"), ("phpunit", "test", "PHPUnit"), ("testify", "test", "testify"),
    ("storybook", "test", "Storybook"),
]

COMPOSE_DB = {"postgres": "postgres", "postgis": "postgres", "timescale": "postgres", "mysql": "mysql",
              "mariadb": "mysql", "mongo": "mongodb", "mssql": "sqlserver", "redis": "redis",
              "oracle": "oracle", "cassandra": "cassandra", "elasticsearch": "elasticsearch",
              "cockroach": "postgres", "clickhouse": "clickhouse"}
URL_DB = {"postgres": "postgres", "postgresql": "postgres", "mysql": "mysql", "mariadb": "mysql",
          "mongodb": "mongodb", "mongodb+srv": "mongodb", "sqlserver": "sqlserver", "mssql": "sqlserver",
          "sqlite": "sqlite", "file": "sqlite", "redis": "redis", "oracle": "oracle"}


def _read(p: Path, limit: int = 400_000) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def _find(root: Path, patterns: list[str], max_depth: int = 4) -> list[Path]:
    out: list[Path] = []
    skip = {"node_modules", ".git", "dist", "build", "target", "vendor", ".venv", "venv", "bin", "obj"}
    for pat in patterns:
        for p in root.rglob(pat):
            rel = p.relative_to(root).parts
            if len(rel) <= max_depth + 1 and not (set(rel) & skip):
                out.append(p)
    return out


def _dep_names(root: Path) -> tuple[set[str], dict]:
    deps: set[str] = set()
    scripts: dict[str, str] = {}
    for pj in _find(root, ["package.json"]):
        try:
            data = json.loads(_read(pj))
        except json.JSONDecodeError:
            continue
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            deps |= {d.lower() for d in (data.get(key) or {})}
        if pj.parent == root:
            scripts = data.get("scripts") or {}
    for f in _find(root, ["requirements*.txt", "pyproject.toml", "Pipfile", "setup.py", "setup.cfg"]):
        for m in re.finditer(r"(?m)^\s*\"?([A-Za-z0-9_.\-\[\]]+)", _read(f)):
            deps.add(m.group(1).lower().split("[")[0])
    for f in _find(root, ["pom.xml"]):
        deps |= {m.lower() for m in re.findall(r"<artifactId>([^<]+)</artifactId>", _read(f))}
        deps |= {m.lower() for m in re.findall(r"<groupId>([^<]+)</groupId>", _read(f))}
    for f in _find(root, ["build.gradle", "build.gradle.kts"]):
        deps |= {m.lower() for m in re.findall(r"['\"]([\w.\-]+:[\w.\-]+)", _read(f))}
    for f in _find(root, ["*.csproj", "Directory.Packages.props"]):
        deps |= {m.lower() for m in re.findall(r"Include=\"([^\"]+)\"", _read(f))}
    for f in _find(root, ["go.mod"]):
        deps |= {m.lower() for m in re.findall(r"(?m)^\s*(?:require\s+)?([\w.\-]+/[\w.\-/]+)\s+v", _read(f))}
    for f in _find(root, ["Gemfile"]):
        deps |= {m.lower() for m in re.findall(r"gem\s+['\"]([\w\-]+)", _read(f))}
    for f in _find(root, ["composer.json"]):
        try:
            data = json.loads(_read(f))
            deps |= {d.lower() for d in {**(data.get("require") or {}), **(data.get("require-dev") or {})}}
        except json.JSONDecodeError:
            pass
    for f in _find(root, ["Cargo.toml"]):
        deps |= {m.lower() for m in re.findall(r"(?m)^([\w\-]+)\s*=", _read(f))}
    for f in _find(root, ["pubspec.yaml"]):
        deps.add("flutter") if "flutter" in _read(f) else None
    return deps, scripts


def _match_dep(sig: str, deps: set[str]) -> bool:
    for d in deps:
        if ":" in d:  # gradle group:artifact
            art = d.split(":")[1]
            if art == sig or art.startswith(sig + "-") or sig in art.split("-"):
                return True
            continue
        if d == sig or d.endswith("/" + sig) or d.startswith(sig + "-") or d.startswith(sig + ".") \
                or ("/" in sig and sig in d):
            return True
    return False


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=20,
                              encoding="utf-8", errors="replace").stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def detect_stack(root: Path, index: CodeIndex | None = None) -> dict:
    root = root.resolve()
    deps, scripts = _dep_names(root)
    found: dict[str, list[str]] = {}

    def add(cat: str, label: str):
        lst = found.setdefault(cat, [])
        if label not in lst:
            lst.append(label)

    for sig, cat, label in DEP_SIGNALS:
        if _match_dep(sig, deps):
            add(cat, label)

    # databases from infra/config
    for f in _find(root, ["docker-compose*.y*ml", "compose*.y*ml"]):
        for img in re.findall(r"image:\s*['\"]?([\w./\-]+)", _read(f)):
            for k, v in COMPOSE_DB.items():
                if k in img.lower():
                    add("database", v)
    for f in _find(root, ["schema.prisma"]):
        m = re.search(r"provider\s*=\s*\"(\w+)\"", _read(f).split("datasource", 1)[-1])
        if m:
            add("database", {"postgresql": "postgres", "mysql": "mysql", "sqlserver": "sqlserver",
                             "mongodb": "mongodb", "sqlite": "sqlite", "cockroachdb": "postgres"}.get(m.group(1), m.group(1)))
    for f in _find(root, [".env.example", ".env.sample", ".env.template", "application*.properties",
                          "application*.yml", "application*.yaml", "appsettings*.json", "config/database.yml",
                          "settings.py", "knexfile.*", "ormconfig.*", "alembic.ini"]):
        text = _read(f)
        for scheme in re.findall(r"([a-z+]+)://", text):
            if scheme in URL_DB:
                add("database", URL_DB[scheme])
        for j in re.findall(r"jdbc:(\w+)", text):
            add("database", URL_DB.get(j, j))
        if re.search(r"Server=.+;(?:Initial Catalog|Database)=", text):
            add("database", "sqlserver")
        if "django.db.backends.postgresql" in text:
            add("database", "postgres")
        if "django.db.backends.mysql" in text:
            add("database", "mysql")

    # CI / ticket systems
    ci, tickets = [], []
    if (root / ".github" / "workflows").exists():
        ci.append("GitHub Actions")
    if (root / ".github").exists():
        tickets.append("github")
    if (root / "azure-pipelines.yml").exists() or (root / ".azuredevops").exists():
        ci.append("Azure Pipelines")
        tickets.append("azure-devops")
    if (root / ".gitlab-ci.yml").exists():
        ci.append("GitLab CI")
        tickets.append("gitlab")
    if (root / "Jenkinsfile").exists():
        ci.append("Jenkins")
    if (root / "bitbucket-pipelines.yml").exists():
        ci.append("Bitbucket Pipelines")
        tickets.append("jira")
    log = _git(root, "log", "-200", "--pretty=%s") + _git(root, "branch", "-a")
    keys = Counter(re.findall(r"\b([A-Z][A-Z0-9]{1,9})-\d+\b", log))
    if "AB#" in log:
        tickets.insert(0, "azure-devops")
    if keys:
        tickets.insert(0, "jira")  # PROJ-123 style keys (Jira, also Linear/YouTrack)
    remote = _git(root, "remote", "-v")
    if "dev.azure.com" in remote or "visualstudio.com" in remote:
        tickets.insert(0, "azure-devops")
    elif "github.com" in remote and "github" not in tickets:
        tickets.append("github")
    elif "gitlab" in remote and "gitlab" not in tickets:
        tickets.append("gitlab")
    tickets = list(dict.fromkeys(tickets))

    design = ["figma"] if re.search(r"figma\.com/(file|design)/", _read(root / "README.md")) else []

    languages = []
    if index is not None and index.is_built():
        languages = [l for l in index.stats()["languages"] if l["lang"] not in ("text", "markdown", "json", "yaml")]

    commands = _commands(root, scripts, found)
    projects = _subprojects(root, found)
    frontend = bool(found.get("frontend") or found.get("mobile"))
    backend = bool(found.get("backend") or found.get("orm") or found.get("database"))
    return {
        "languages": languages[:12],
        "frontend": found.get("frontend", []), "mobile": found.get("mobile", []),
        "backend": found.get("backend", []), "orm": found.get("orm", []),
        "databases": found.get("database", []), "test_frameworks": found.get("test", []),
        "ci": ci, "ticket_systems": tickets, "ticket_keys": [k for k, _ in keys.most_common(3)],
        "design": design, "has_frontend": frontend, "has_backend": backend,
        "commands": commands, "projects": projects,
    }


MANIFESTS = ["package.json", "pyproject.toml", "requirements.txt", "pom.xml", "build.gradle",
             "build.gradle.kts", "*.csproj", "go.mod", "Cargo.toml", "Gemfile", "composer.json", "pubspec.yaml"]


def _subprojects(root: Path, found: dict) -> list[dict]:
    """Each folder (below root) that has its own manifest, with its own run commands."""
    dirs: dict[Path, list[str]] = {}
    for p in _find(root, MANIFESTS, 3):
        if p.parent != root:
            dirs.setdefault(p.parent, []).append(p.name)
    out = []
    for d, manifests in sorted(dirs.items()):
        scripts: dict = {}
        if "package.json" in manifests:
            try:
                scripts = json.loads(_read(d / "package.json")).get("scripts") or {}
            except json.JSONDecodeError:
                pass
        sub_found = {"backend": found.get("backend", []),
                     "test": found.get("test", []) if "package.json" in manifests else []}
        out.append({"path": d.relative_to(root).as_posix(), "manifests": sorted(manifests),
                    "commands": _commands(d, scripts, sub_found)})
    return out[:30]


def _commands(root: Path, scripts: dict, found: dict) -> dict:
    pm = "npm"
    if (root / "pnpm-lock.yaml").exists():
        pm = "pnpm"
    elif (root / "yarn.lock").exists():
        pm = "yarn"
    elif (root / "bun.lockb").exists() or (root / "bun.lock").exists():
        pm = "bun"
    run = (lambda s: f"{pm} run {s}") if pm != "yarn" else (lambda s: f"yarn {s}")
    cmds: dict[str, str] = {}
    for key, names in {"install": [], "dev": ["dev", "start", "serve"], "build": ["build"],
                       "test": ["test", "test:unit"], "lint": ["lint"], "typecheck": ["typecheck", "type-check", "tsc"],
                       "e2e": ["e2e", "test:e2e", "playwright"]}.items():
        for n in names:
            if n in scripts:
                cmds[key] = run(n)
                break
    if scripts:
        cmds["install"] = f"{pm} install"
    if (root / "pyproject.toml").exists() or (root / "requirements.txt").exists():
        cmds.setdefault("test", "pytest")
        cmds.setdefault("install", "pip install -r requirements.txt" if (root / "requirements.txt").exists() else "pip install -e .")
    if (root / "manage.py").exists():
        cmds.setdefault("dev", "python manage.py runserver")
    if (root / "pom.xml").exists():
        cmds.setdefault("build", "mvn -q package -DskipTests")
        cmds.setdefault("test", "mvn test")
        cmds.setdefault("dev", "mvn spring-boot:run" if "Spring Boot" in found.get("backend", []) else "mvn exec:java")
    if list(root.glob("build.gradle*")):
        g = "./gradlew" if (root / "gradlew").exists() else "gradle"
        cmds.setdefault("build", f"{g} build -x test")
        cmds.setdefault("test", f"{g} test")
    if list(root.glob("*.sln")) or _find(root, ["*.csproj"], 2):
        cmds.setdefault("build", "dotnet build")
        cmds.setdefault("test", "dotnet test")
        cmds.setdefault("dev", "dotnet run")
    if (root / "go.mod").exists():
        cmds.setdefault("build", "go build ./...")
        cmds.setdefault("test", "go test ./...")
    if (root / "Cargo.toml").exists():
        cmds.setdefault("build", "cargo build")
        cmds.setdefault("test", "cargo test")
    if (root / "Makefile").exists():
        targets = re.findall(r"(?m)^([a-zA-Z][\w\-]*):", _read(root / "Makefile"))
        for k in ("test", "build", "lint", "dev"):
            if k in targets:
                cmds.setdefault(k, f"make {k}")
    if "Playwright" in found.get("test", []):
        cmds.setdefault("e2e", "npx playwright test")
    return cmds


# ---------------------------------------------------------------------------
# Conventions: learned from the code itself
# ---------------------------------------------------------------------------

def _case(name: str) -> str:
    if re.fullmatch(r"[a-z]+(_[a-z0-9]+)+", name):
        return "snake_case"
    if re.fullmatch(r"[a-z]+([A-Z][a-z0-9]*)+", name):
        return "camelCase"
    if re.fullmatch(r"([A-Z][a-z0-9]+){2,}", name):
        return "PascalCase"
    if re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)+", name):
        return "kebab-case"
    return ""


def analyze_conventions(index: CodeIndex) -> dict:
    db = index.db
    fn_cases: Counter; cls_cases: Counter; file_cases: Counter
    fn_cases, cls_cases, file_cases = Counter(), Counter(), Counter()
    for (name,) in db.execute("SELECT name FROM symbols WHERE kind IN ('function','method') LIMIT 5000"):
        c = _case(name)
        if c:
            fn_cases[c] += 1
    for (name,) in db.execute("SELECT name FROM symbols WHERE kind IN ('class','interface','struct') LIMIT 3000"):
        c = _case(name)
        if c:
            cls_cases[c] += 1
    paths = [r[0] for r in db.execute("SELECT path FROM files WHERE parser!='text'")]
    for p in paths:
        c = _case(Path(p).stem.split(".")[0])
        if c:
            file_cases[c] += 1

    test_rx = re.compile(r"(^|/)(tests?|__tests__|spec|specs|e2e|integration)(/|$)|"
                         r"(\.|_)(test|spec)\.[a-z]+$|^test_.*\.py$|/test_[^/]+\.py$|Tests?\.(cs|java|kt)$")
    tests = [p for p in paths if test_rx.search(p)]
    test_dirs: Counter = Counter()
    test_styles: Counter = Counter()
    for t in tests:
        parts = t.split("/")
        test_dirs["/".join(parts[:2]) if len(parts) > 2 else parts[0]] += 1
        if re.search(r"\.(test|spec)\.[a-z]+$", t):
            test_styles["co-located *.test/*.spec files" if not re.search(r"(^|/)(tests?|__tests__|e2e|specs?)/", t)
                        else "*.test/*.spec files in test folders"] += 1
        elif re.search(r"(^|/)test_[^/]+\.py$", t):
            test_styles["pytest test_*.py files"] += 1
        elif re.search(r"Tests?\.(cs|java|kt)$", t):
            test_styles["*Test(s) classes"] += 1
        else:
            test_styles["files under test folders"] += 1

    def examples(pattern: str, kinds=("class", "function", "interface", "struct"), n=3):
        rows = db.execute(
            f"SELECT path, name, qualname, start_line FROM symbols WHERE kind IN ({','.join('?' * len(kinds))}) "
            "AND parent='' AND (path LIKE ? OR name LIKE ?) ORDER BY tokens DESC LIMIT 60",
            (*kinds, f"%{pattern}%", f"%{pattern}%")).fetchall()
        out = []
        for r in rows:
            base = r["path"].rsplit("/", 1)[-1].lower()
            folder = r["path"].rsplit("/", 2)[-2].lower() if r["path"].count("/") >= 1 else ""
            nm = r["name"].lower()
            hit = (nm.startswith(pattern) if pattern == "use" else pattern in nm) or pattern in base \
                or folder.startswith(pattern)
            if hit:
                out.append(f"{r['path']}:{r['start_line']} ({r['qualname']})")
            if len(out) >= n:
                break
        return out

    archetypes = {}
    for label, pat in [("Controllers / routes", "controller"), ("Routes", "route"), ("Services", "service"),
                       ("Repositories / DAOs", "repositor"), ("Models / entities", "model"),
                       ("Entities", "entit"), ("Components", "component"), ("Hooks", "use"),
                       ("Pages / views", "page"), ("Handlers", "handler"), ("Middleware", "middleware"),
                       ("DTOs / schemas", "dto"), ("Schemas", "schema"), ("Migrations", "migration"),
                       ("Tests", "test")]:
        ex = examples(pat)
        if ex:
            archetypes[label] = ex

    top_dirs = Counter(p.split("/")[0] for p in paths if "/" in p)
    lint = [n for n in (".eslintrc", ".eslintrc.js", ".eslintrc.json", ".eslintrc.cjs", "eslint.config.js",
                        "eslint.config.mjs", ".prettierrc", ".prettierrc.json", "prettier.config.js", "biome.json",
                        "ruff.toml", ".flake8", ".pylintrc", "mypy.ini", ".editorconfig", "checkstyle.xml",
                        ".golangci.yml", "rustfmt.toml", ".rubocop.yml", "tsconfig.json", "stylecop.json",
                        ".stylelintrc") if (index.root / n).exists()]
    if (index.root / "pyproject.toml").exists():
        pp = _read(index.root / "pyproject.toml")
        lint += [t for t in ("ruff", "black", "isort", "mypy") if f"[tool.{t}" in pp]

    def top(c: Counter):
        return c.most_common(1)[0][0] if c else "mixed/unknown"

    return {
        "function_naming": top(fn_cases), "type_naming": top(cls_cases), "file_naming": top(file_cases),
        "test_files": len(tests), "source_files": len(paths) - len(tests),
        "test_style": top(test_styles), "test_locations": [d for d, _ in test_dirs.most_common(4)],
        "top_level_dirs": [d for d, _ in top_dirs.most_common(10)],
        "lint_and_format": lint, "archetypes": archetypes,
    }

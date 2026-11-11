"""Deterministic documentation facts, extracted locally from the repo.

The doc-writer model receives these instead of reading the codebase: every endpoint, function,
setting, route, test file and resource comes with a file:line reference, so documents are grounded
in code and can be checked for completeness by `specanvil docs check`.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict

from .detect import analyze_conventions, detect_stack
from .index import GENERATED_RX, TEST_RX, CodeIndex
from .scanner import is_secret

# ----------------------------------------------------------------------------- patterns

ENDPOINT_PATTERNS = [
    # Express / Koa / Fastify / Hono / Gin / Echo / Chi:  app.get('/x'  router.post("/x"  r.GET("/x"
    re.compile(r"""(?<![@\w.])(?:app|router|route|r|api|server|fastify|e|g|group|v\d)\.(get|post|put|patch|delete|options|head|all|GET|POST|PUT|PATCH|DELETE)\(\s*['"`]([^'"`]+)['"`]"""),
    # FastAPI / Flask / Sanic decorators: @app.get("/x")  @router.post("/x")  @bp.route("/x", methods=[...])
    re.compile(r"""@\w+\.(get|post|put|patch|delete|route|api_route)\(\s*['"]([^'"]*)['"]"""),
    # Spring: @GetMapping("/x") @PostMapping(value = "/x") @RequestMapping("/x")
    re.compile(r"""@(Get|Post|Put|Patch|Delete|Request)Mapping\s*\(\s*(?:value\s*=\s*|path\s*=\s*)?\{?\s*"([^"]*)\""""),
    # ASP.NET attributes: [HttpGet("x")] [Route("api/[controller]")]
    re.compile(r"""\[(HttpGet|HttpPost|HttpPut|HttpPatch|HttpDelete|Route)\(\s*"([^"]*)"\s*\)"""),
    # ASP.NET minimal APIs: app.MapGet("/x"
    re.compile(r"""\.Map(Get|Post|Put|Patch|Delete)\(\s*"([^"]+)\""""),
    # NestJS: @Get('x') @Post() — path optional
    re.compile(r"""@(Get|Post|Put|Patch|Delete)\(\s*['"]([^'"]*)['"]\s*\)"""),
    # Django: path("x/", view)  re_path(r"^x/$", view)
    re.compile(r"""\b(path|re_path)\(\s*r?['"]([^'"]*)['"]\s*,"""),
    # Laravel / Rails: Route::get('/x'  get '/x', to:
    re.compile(r"""Route::(get|post|put|patch|delete|any)\(\s*['"]([^'"]+)['"]"""),
    re.compile(r"""^\s*(get|post|put|patch|delete)\s+['"](/[^'"]*)['"]"""),
    # bare annotations without a path: Spring @PostMapping / NestJS @Get()  (index 9, prefixable)
    re.compile(r"""@(Get|Post|Put|Patch|Delete)(?:Mapping\b(?!\s*\()|\(\s*\))()"""),
]
CLASS_PREFIX_RX = re.compile(r"""@(?:RequestMapping|Controller)\(\s*(?:value\s*=\s*|path\s*=\s*)?['"]([^'"]*)['"]|\[Route\(\s*"([^"]*)"\s*\)\]|APIRouter\([^)]*prefix\s*=\s*['"]([^'"]*)['"]""")

ENV_PATTERNS = [
    re.compile(r"""process\.env\.([A-Z][A-Z0-9_]{2,})"""),
    re.compile(r"""process\.env\[\s*['"]([A-Z][A-Z0-9_]{2,})['"]\s*\]"""),
    re.compile(r"""import\.meta\.env\.([A-Z][A-Z0-9_]{2,})"""),
    re.compile(r"""os\.(?:environ\.get|getenv|environ)\s*[\(\[]\s*['"]([A-Z][A-Z0-9_]{2,})['"]"""),
    re.compile(r"""(?:System\.getenv|Environment\.GetEnvironmentVariable|os\.Getenv|getenv)\(\s*"([A-Z][A-Z0-9_]{2,})\""""),
    re.compile(r"""ENV\[\s*['"]([A-Z][A-Z0-9_]{2,})['"]\s*\]"""),
    re.compile(r"""\$\{([A-Z][A-Z0-9_]{2,})(?::[^}]*)?\}"""),  # Spring / compose / shell
    re.compile(r"""(?:Configuration|config|_config)\[\s*"([A-Za-z][\w:]{2,})"\s*\]"""),  # .NET IConfiguration
]
SETTINGS_FIELD_RX = re.compile(r"""^\s+([A-Z][A-Z0-9_]{2,})\s*:\s*[\w\[\]|. ]+(?:=|$)""", re.M)  # pydantic BaseSettings

AZ_PY_RX = re.compile(r"""@\w+\.(route|timer_trigger|queue_trigger|blob_trigger|service_bus_queue_trigger|service_bus_topic_trigger|event_hub_message_trigger|event_grid_trigger|cosmos_db_trigger|function_name)\(([^)]*)\)""")
AZ_CS_FN_RX = re.compile(r"""\[(?:Function|FunctionName)\(\s*(?:nameof\((\w+)\)|"([^"]+)")\s*\)\]""")
AZ_CS_TRIGGER_RX = re.compile(r"""\[(HttpTrigger|TimerTrigger|QueueTrigger|BlobTrigger|ServiceBusTrigger|EventHubTrigger|EventGridTrigger|CosmosDBTrigger|DurableClient|OrchestrationTrigger|ActivityTrigger)\(([^\]]*)\)\]""")
AZ_NODE_RX = re.compile(r"""\bapp\.(http|timer|storageQueue|storageBlob|serviceBusQueue|serviceBusTopic|eventHub|eventGrid|cosmosDB)\(\s*['"]([^'"]+)['"]\s*,\s*\{([^}]*)""", re.S)

TEST_CASE_RX = re.compile(r"""^\s*(?:it|test|specify|scenario)(?:\.\w+)?\(\s*['"`]|^\s*(?:async\s+)?def\s+test_\w+|@Test\b|\[(?:Fact|Theory|Test|TestMethod|TestCase)\b|^\s*Scenario(?: Outline)?:|func\s+Test\w+\(""", re.M)
TAG_RX = re.compile(r"""(?<![\w@])@(smoke|regression|sanity|e2e|critical|p[0-3]|slow|wip|flaky|api|ui|mobile)\b""", re.I)

TF_RES_RX = re.compile(r"""^resource\s+"([\w-]+)"\s+"([\w-]+)\"""", re.M)
TF_MOD_RX = re.compile(r"""^module\s+"([\w-]+)\"""", re.M)
BICEP_RX = re.compile(r"""^resource\s+(\w+)\s+'([\w.\-/]+)@""", re.M)
K8S_RX = re.compile(r"""^kind:\s*(\w+)\s*\n(?:.*\n){0,6}?\s*name:\s*([\w.-]+)""", re.M)
CFN_RX = re.compile(r"""^\s{2}(\w+):\s*\n\s{4}Type:\s*['"]?(AWS::[\w:]+)""", re.M)

CLI_RX = [
    re.compile(r"""add_parser\(\s*['"]([\w-]+)['"]"""),                      # argparse
    re.compile(r"""@(?:\w+\.)?command\(\s*(?:name\s*=\s*)?['"]([\w-]+)['"]"""),  # click / typer
    re.compile(r"""\.command\(\s*['"]([\w-]+)"""),                           # commander / yargs
    re.compile(r"""Use:\s*"([\w-]+)"""),                                     # cobra
]
QUEUE_RX = re.compile(r"""(?:queue|topic|subscription|queueName|topicName|QueueName|TopicName)\s*[=:(]\s*['"]([\w.\-]{3,})['"]""")
DAG_RX = re.compile(r"""DAG\(\s*(?:dag_id\s*=\s*)?['"]([\w.\-]+)['"]|@dag\(|dag_id\s*=\s*['"]([\w.\-]+)['"]""")

AUTH_HINTS = {
    "JWT": r"\bjwt\b|jsonwebtoken|JwtBearer|PyJWT|jose\b", "OAuth2 / OIDC": r"OAuth2|oauth|openid|OIDC|AddOpenIdConnect",
    "Azure AD / Entra ID": r"Microsoft\.Identity|msal|AzureAd|Entra", "Session cookies": r"express-session|SessionMiddleware|cookie-session",
    "API keys": r"x-api-key|ApiKey|api_key_header", "NextAuth / Auth.js": r"next-auth|@auth/", "Passport": r"\bpassport\b",
    "Spring Security": r"spring-boot-starter-security|SecurityFilterChain|@PreAuthorize", "ASP.NET Identity": r"AddIdentity|\[Authorize",
    "Django auth": r"django\.contrib\.auth|login_required", "Firebase Auth": r"firebase/auth|firebase_admin\.auth",
    "Cognito": r"cognito", "Keycloak": r"keycloak", "Basic auth": r"HTTPBasic|BasicAuth",
}


# ----------------------------------------------------------------------------- helpers

class FactContext:
    """Reads each indexed file at most once per fact extraction run."""

    def __init__(self, ix: CodeIndex):
        self.ix = ix
        self.root = ix.root
        self.files = [dict(r) for r in ix.db.execute("SELECT path, lang, parser FROM files ORDER BY path")]
        self._text: dict[str, str] = {}

    def text(self, path: str) -> str:
        if path not in self._text:
            try:
                self._text[path] = (self.root / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                self._text[path] = ""
        return self._text[path]

    def code_files(self, include_tests: bool = False):
        for f in self.files:
            if f["parser"] == "text" or GENERATED_RX.search(f["path"]):
                continue
            if not include_tests and TEST_RX.search(f["path"]):
                continue
            yield f["path"]

    def all_files(self, suffixes: tuple[str, ...] = (), names: tuple[str, ...] = ()):
        for f in self.files:
            p = f["path"]
            n = p.rsplit("/", 1)[-1]
            if (suffixes and p.endswith(suffixes)) or (names and n in names):
                yield p


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _handler(ix: CodeIndex, path: str, line: int) -> str:
    """The function a route decorator/annotation belongs to: the next symbol defined within a few lines,
    else the smallest symbol containing the line (annotations inside a method's range)."""
    row = ix.db.execute("SELECT qualname FROM symbols WHERE path=? AND start_line<=? AND end_line>=? AND start_line>=? "
                        "AND kind IN ('function','method') ORDER BY start_line DESC LIMIT 1",
                        (path, line, line, line - 3)).fetchone()  # annotation is the first line(s) of the method
    if row:
        return row["qualname"]
    row = ix.db.execute("SELECT qualname FROM symbols WHERE path=? AND start_line>? AND start_line<=? "
                        "AND kind IN ('function','method') ORDER BY start_line LIMIT 1", (path, line, line + 12)).fetchone()
    if row:
        return row["qualname"]
    row = ix.db.execute("SELECT qualname FROM symbols WHERE path=? AND start_line<=? AND end_line>=? AND kind IN "
                        "('function','method') ORDER BY (end_line-start_line) LIMIT 1", (path, line, line)).fetchone()
    return row["qualname"] if row else ""


def _enclosing(ix: CodeIndex, path: str, line: int) -> str:
    row = ix.enclosing_symbol(path, line)
    return row["qualname"] if row else ""


# ----------------------------------------------------------------------------- extractors

def endpoints(fc: FactContext) -> list[dict]:
    out, seen = [], set()
    for path in fc.code_files():
        text = fc.text(path)
        if not text:
            continue
        prefix_m = CLASS_PREFIX_RX.search(text)
        prefix = next((g for g in prefix_m.groups() if g), "") if prefix_m else ""
        for i, rx in enumerate(ENDPOINT_PATTERNS):
            prefixable = i in (1, 2, 3, 5, 9)  # decorator/attribute styles inherit the router/controller prefix
            for m in rx.finditer(text):
                verb, route = m.group(1), m.group(2) or ""
                v = verb.upper().replace("HTTP", "").replace("MAPPING", "")
                v = {"REQUEST": "ANY", "ROUTE": "ANY", "PATH": "ANY", "RE_PATH": "ANY", "API_ROUTE": "ANY",
                     "ALL": "ANY"}.get(v, v)
                if v == "ANY" and rx.pattern.startswith(r"\[") and "Route" in verb:
                    continue  # [Route] on a class is a prefix, already applied
                if prefix and prefixable and not route.startswith(prefix):
                    full = prefix.rstrip("/") + "/" + route.lstrip("/")
                else:
                    full = route or prefix
                if v == "ANY" and prefix and full.rstrip("/") == prefix.rstrip("/"):
                    continue  # class-level @RequestMapping / [Route]: that's the prefix, not an endpoint
                if not full.startswith("/") and not full.startswith("^"):
                    full = "/" + full
                ln = _line(text, m.start())
                key = (v, full, path)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"method": v, "path": full, "handler": _handler(fc.ix, path, ln), "at": f"{path}:{ln}"})
    return out[:500]


def env_vars(fc: FactContext) -> list[dict]:
    found: dict[str, dict] = {}

    def add(name: str, where: str, source: str):
        if len(name) < 3 or name in ("NODE_ENV",) and source == "code":
            pass
        e = found.setdefault(name, {"name": name, "used_at": [], "declared_in": []})
        tgt = e["used_at"] if source == "code" else e["declared_in"]
        if where not in tgt and len(tgt) < 5:
            tgt.append(where)

    for path in fc.code_files(include_tests=False):
        text = fc.text(path)
        for rx in ENV_PATTERNS:
            for m in rx.finditer(text):
                add(m.group(1), f"{path}:{_line(text, m.start())}", "code")
        if re.search(r"BaseSettings|@ConfigurationProperties|class Settings", text):
            for m in SETTINGS_FIELD_RX.finditer(text):
                add(m.group(1), f"{path}:{_line(text, m.start())}", "code")
    # declarations: .env.example-style files, docker-compose, app settings samples (never real secrets)
    for path in fc.all_files(names=(".env.example", ".env.sample", ".env.template", ".env.dist",
                                    "local.settings.sample.json", "local.settings.example.json")):
        text = fc.text(path)
        if path.endswith(".json"):
            try:
                for k in (json.loads(text).get("Values") or {}):
                    add(k, path, "decl")
            except (json.JSONDecodeError, AttributeError):
                pass
        else:
            for m in re.finditer(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]{2,})\s*=", text, re.M):
                add(m.group(1), f"{path}:{_line(text, m.start())}", "decl")
    for path in fc.all_files(suffixes=("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")):
        text = fc.text(path)
        for m in re.finditer(r"^\s+-?\s*([A-Z][A-Z0-9_]{2,})[:=]", text, re.M):
            add(m.group(1), f"{path}:{_line(text, m.start())}", "decl")
    noise = {"HTTP", "GET", "POST", "PUT", "DELETE", "PATH", "HOME", "USER", "PWD", "TRUE", "FALSE", "NULL"}
    return sorted((v for k, v in found.items() if k not in noise), key=lambda x: x["name"])[:400]


def azure_functions(fc: FactContext) -> list[dict]:
    out = []
    # v1-v3 style: <Function>/function.json
    for path in fc.all_files(names=("function.json",)):
        try:
            data = json.loads(fc.text(path))
        except json.JSONDecodeError:
            continue
        b = data.get("bindings") or []
        trig = next((x for x in b if str(x.get("type", "")).endswith("Trigger")), {})
        out.append({"name": path.rsplit("/", 2)[-2] if "/" in path else path, "trigger": trig.get("type"),
                    "detail": trig.get("schedule") or trig.get("route") or trig.get("queueName") or trig.get("path")
                    or trig.get("topicName") or "", "bindings": [f"{x.get('direction')}:{x.get('type')}" for x in b],
                    "at": path})
    for path in fc.code_files():
        text = fc.text(path)
        if "azure" not in text.lower() and "Function" not in text and "app." not in text:
            continue
        if path.endswith(".py"):
            pending_name = None
            for m in AZ_PY_RX.finditer(text):
                kind, args = m.group(1), m.group(2)
                if kind == "function_name":
                    pending_name = re.search(r"""['"]([^'"]+)['"]""", args)
                    pending_name = pending_name.group(1) if pending_name else None
                    continue
                ln = _line(text, m.start())
                handler = _enclosing(fc.ix, path, ln + 1) or _enclosing(fc.ix, path, ln + 2)
                detail = re.search(r"""(?:route|schedule|queue_name|path|topic_name|event_hub_name|container_name)\s*=\s*['"]([^'"]+)['"]""", args)
                out.append({"name": pending_name or handler or kind, "trigger": kind, "detail": detail.group(1) if detail else "",
                            "bindings": [], "at": f"{path}:{ln}"})
                pending_name = None
        elif path.endswith(".cs"):
            for m in AZ_CS_FN_RX.finditer(text):
                name = m.group(2) or m.group(1)
                window = text[m.end():m.end() + 1500]
                t = AZ_CS_TRIGGER_RX.search(window)
                ln = _line(text, m.start())
                out.append({"name": name, "trigger": t.group(1) if t else "", "detail": (t.group(2)[:120] if t else ""),
                            "bindings": re.findall(r"\[(\w+(?:Input|Output))\(", window)[:6], "at": f"{path}:{ln}"})
        elif path.endswith((".js", ".ts", ".mjs", ".cjs")) and "@azure/functions" in text:
            for m in AZ_NODE_RX.finditer(text):
                body = m.group(3)
                detail = re.search(r"""(?:route|schedule|queueName|path|topicName)\s*:\s*['"]([^'"]+)['"]""", body)
                out.append({"name": m.group(2), "trigger": m.group(1), "detail": detail.group(1) if detail else "",
                            "bindings": [], "at": f"{path}:{_line(text, m.start())}"})
    return out


def host_config(fc: FactContext) -> dict:
    for path in fc.all_files(names=("host.json",)):
        try:
            data = json.loads(fc.text(path))
        except json.JSONDecodeError:
            continue
        return {"at": path, "version": data.get("version"), "functionTimeout": data.get("functionTimeout"),
                "extensionBundle": (data.get("extensionBundle") or {}).get("version"),
                "extensions": list((data.get("extensions") or {}).keys()),
                "retry": data.get("retry"), "logging": list((data.get("logging") or {}).keys())}
    return {}


def lambda_functions(fc: FactContext) -> list[dict]:
    out = []
    for path in fc.all_files(names=("serverless.yml", "serverless.yaml")):
        text = fc.text(path)
        block = text.split("\nfunctions:", 1)[-1] if "\nfunctions:" in text else ""
        for m in re.finditer(r"^  ([\w-]+):\s*\n((?:    .*\n|\s*\n)+)", block, re.M):
            handler = re.search(r"handler:\s*(\S+)", m.group(2))
            events = re.findall(r"-\s*(http|httpApi|sqs|sns|schedule|s3|stream|eventBridge|alb|websocket)\b", m.group(2))
            out.append({"name": m.group(1), "handler": handler.group(1) if handler else "", "events": events, "at": path})
    for path in fc.all_files(suffixes=("template.yaml", "template.yml")):
        text = fc.text(path)
        for m in re.finditer(r"^  (\w+):\s*\n\s+Type:\s*AWS::Serverless::Function", text, re.M):
            seg = text[m.end():m.end() + 1500]
            handler = re.search(r"Handler:\s*(\S+)", seg)
            out.append({"name": m.group(1), "handler": handler.group(1) if handler else "",
                        "events": re.findall(r"Type:\s*(Api|HttpApi|SQS|SNS|Schedule|S3|EventBridgeRule)", seg),
                        "at": f"{path}:{_line(text, m.start())}"})
    return out


def routes(fc: FactContext) -> list[dict]:
    out = []
    for f in fc.files:
        p = f["path"]
        m = re.search(r"(?:^|/)(?:src/)?(app|pages)/(.+)\.(tsx|jsx|ts|js|vue|svelte)$", p)
        if m and not re.search(r"/(api|components|_components)/", p) and not TEST_RX.search(p):
            seg, rest = m.group(1), m.group(2)
            if seg == "app" and not rest.endswith(("page", "+page")):
                continue
            if seg == "pages" and rest.split("/")[-1].startswith("_"):
                continue
            r = "/" + re.sub(r"(^|/)(index|page|\+page)$", "", rest)
            r = re.sub(r"\(([^)]+)\)/?", "", r)  # route groups
            out.append({"route": r.rstrip("/") or "/", "file": p, "kind": "file-based"})
        # TanStack Router / Remix style: routes/_layout/items.tsx
        m2 = re.search(r"(?:^|/)routes/(.+)\.(tsx|jsx|ts|js)$", p)
        if m2 and not TEST_RX.search(p) and "__root" not in p:
            r = "/" + "/".join(s for s in m2.group(1).split("/") if not s.startswith("_")).replace("index", "")
            r = r.replace("$", ":")
            out.append({"route": r.rstrip("/") or "/", "file": p, "kind": "file-based"})
    for path in fc.code_files():
        if not path.endswith((".tsx", ".jsx", ".ts", ".js", ".vue")):
            continue
        text = fc.text(path)
        for m in re.finditer(r"""<Route[^>]*\spath=["']([^"']+)["']|\bpath:\s*['"]([^'"]*)['"]\s*,\s*(?:component|element|loadComponent|loadChildren|children|redirectTo)""", text):
            out.append({"route": m.group(1) or "/" + (m.group(2) or ""), "file": f"{path}:{_line(text, m.start())}",
                        "kind": "config"})
    seen, uniq = set(), []
    for r in out:
        if r["route"] not in seen:
            seen.add(r["route"])
            uniq.append(r)
    return uniq[:300]


def components(fc: FactContext) -> list[dict]:
    rows = fc.ix.db.execute(
        "SELECT s.name, s.path, s.start_line, s.doc FROM symbols s JOIN files f ON f.path=s.path "
        "WHERE f.lang IN ('tsx','jsx','vue','svelte','javascript') AND s.parent='' AND s.kind IN ('function','class') "
        "AND substr(s.name,1,1) BETWEEN 'A' AND 'Z' ORDER BY s.path").fetchall()
    out = []
    for r in rows:
        if TEST_RX.search(r["path"]) or GENERATED_RX.search(r["path"]):
            continue
        out.append({"name": r["name"], "at": f"{r['path']}:{r['start_line']}", "doc": r["doc"] or "",
                    "shared": bool(re.search(r"/(ui|common|shared|design-system|primitives)/", r["path"]))})
    return out[:400]


def tests(fc: FactContext) -> dict:
    files = [f["path"] for f in fc.files if f["parser"] != "text" and TEST_RX.search(f["path"])]
    files += [f["path"] for f in fc.files if f["path"].endswith(".feature")]
    inv, tags_total = [], defaultdict(int)
    for p in files:
        text = fc.text(p)
        n = len(TEST_CASE_RX.findall(text))
        tags = sorted({t.lower() for t in TAG_RX.findall(text)})
        for t in tags:
            tags_total[t] += 1
        browser = re.search(r"@playwright/test|from playwright|cypress|webdriverio|selenium|appium|Capybara|"
                            r"testcafe|nightwatch|codeceptjs", text)
        kind = ("e2e" if browser or re.search(r"(^|/)(e2e|cypress|playwright|ui-tests|acceptance|features)(/|$)|\.feature$", p)
                else "integration" if re.search(r"integration|/it/|api-tests|\.it\.", p, re.I) else "unit")
        inv.append({"file": p, "cases": n, "type": kind, "tags": tags})
    by_type = defaultdict(lambda: {"files": 0, "cases": 0})
    for t in inv:
        by_type[t["type"]]["files"] += 1
        by_type[t["type"]]["cases"] += t["cases"]
    return {"files": inv[:500], "by_type": dict(by_type), "tags": dict(tags_total),
            "config_files": [f["path"] for f in fc.files if re.search(
                r"(playwright|cypress|jest|vitest|karma|wdio|protractor|pytest|cucumber|testng|codecept)\.(config|conf)\.|"
                r"(^|/)(pytest\.ini|conftest\.py|jest\.config\.\w+|\.mocharc\.\w+|behave\.ini|testng\.xml|"
                r"specflow\.json|xunit\.runner\.json)$", f["path"])]}


def page_objects(fc: FactContext) -> list[dict]:
    rows = fc.ix.db.execute(
        "SELECT name, path, start_line FROM symbols WHERE parent='' AND kind='class' AND "
        "(path LIKE '%page%' OR path LIKE '%/pom/%' OR name LIKE '%Page' OR name LIKE '%Screen' OR name LIKE '%Component')"
    ).fetchall()
    return [{"name": r["name"], "at": f"{r['path']}:{r['start_line']}"} for r in rows][:200]


def models(fc: FactContext) -> list[dict]:
    rows = fc.ix.db.execute(
        "SELECT s.name, s.path, s.start_line, s.signature, s.doc FROM symbols s WHERE s.parent='' "
        "AND s.kind IN ('class','struct','interface','record') AND (s.path LIKE '%model%' OR s.path LIKE '%entit%' "
        "OR s.path LIKE '%schema%' OR s.path LIKE '%domain%' OR s.signature LIKE '%(Base)%' OR s.signature LIKE '%Model)%' "
        "OR s.signature LIKE '%table=True%' OR s.signature LIKE '%@Entity%' OR s.signature LIKE '%Document%')").fetchall()
    out = []
    for r in rows:
        if TEST_RX.search(r["path"]) or GENERATED_RX.search(r["path"]):
            continue
        text = fc.text(r["path"])
        # ORM evidence near the class keeps DTOs out
        orm = bool(re.search(r"__tablename__|table=True|@Entity|@Table|Column\(|mapped_column|models\.Model|"
                             r"@Document|Schema\(|DbSet<|@prop\(|model\(|\[Table\(", text))
        out.append({"name": r["name"], "at": f"{r['path']}:{r['start_line']}", "orm": orm, "doc": r["doc"] or ""})
    # Prisma
    for p in fc.all_files(suffixes=(".prisma",)):
        text = fc.text(p)
        for m in re.finditer(r"^model\s+(\w+)\s*\{", text, re.M):
            out.append({"name": m.group(1), "at": f"{p}:{_line(text, m.start())}", "orm": True, "doc": ""})
    orm_only = [m for m in out if m["orm"]]
    return (orm_only or out)[:300]


def migrations(fc: FactContext) -> list[str]:
    return [f["path"] for f in fc.files if re.search(
        r"(^|/)(migrations?|alembic/versions|db/migrate|Migrations|flyway|liquibase|changelog)(/|$)", f["path"])][:300]


def infra_resources(fc: FactContext) -> list[dict]:
    out = []
    for p in fc.all_files(suffixes=(".tf",)):
        text = fc.text(p)
        out += [{"type": m.group(1), "name": m.group(2), "tool": "terraform", "at": f"{p}:{_line(text, m.start())}"}
                for m in TF_RES_RX.finditer(text)]
        out += [{"type": "module", "name": m.group(1), "tool": "terraform", "at": f"{p}:{_line(text, m.start())}"}
                for m in TF_MOD_RX.finditer(text)]
    for p in fc.all_files(suffixes=(".bicep",)):
        text = fc.text(p)
        out += [{"type": m.group(2), "name": m.group(1), "tool": "bicep", "at": f"{p}:{_line(text, m.start())}"}
                for m in BICEP_RX.finditer(text)]
    for p in fc.all_files(suffixes=(".yaml", ".yml")):
        text = fc.text(p)
        if "apiVersion:" in text and "kind:" in text:
            out += [{"type": m.group(1), "name": m.group(2), "tool": "kubernetes", "at": f"{p}:{_line(text, m.start())}"}
                    for m in K8S_RX.finditer(text)]
        if "AWSTemplateFormatVersion" in text or "Transform: AWS::Serverless" in text:
            out += [{"type": m.group(2), "name": m.group(1), "tool": "cloudformation", "at": f"{p}:{_line(text, m.start())}"}
                    for m in CFN_RX.finditer(text)]
    for p in fc.all_files(names=("Dockerfile",)):
        out.append({"type": "container image", "name": p, "tool": "docker", "at": p})
    return out[:400]


def ci(fc: FactContext) -> list[dict]:
    out = []
    for f in fc.files:
        p = f["path"]
        if re.search(r"^\.github/workflows/.+\.ya?ml$|azure-pipelines.*\.ya?ml$|\.gitlab-ci\.yml$|Jenkinsfile$|"
                     r"bitbucket-pipelines\.yml$|\.circleci/config\.yml$|cloudbuild\.ya?ml$", p):
            text = fc.text(p)
            jobs = re.findall(r"^  ([\w-]+):\s*$", text.split("jobs:", 1)[-1], re.M)[:15] if "jobs:" in text else []
            stages = re.findall(r"-\s*stage:\s*([\w-]+)", text)[:15]
            triggers = re.findall(r"^on:\s*\[?([\w, ]+)\]?|^\s+(push|pull_request|schedule|workflow_dispatch):", text, re.M)
            out.append({"file": p, "jobs": jobs or stages, "triggers": sorted({t for g in triggers for t in g if t})})
    return out


def cli_commands(fc: FactContext) -> list[dict]:
    out, seen = [], set()
    for path in fc.code_files():
        text = fc.text(path)
        for rx in CLI_RX:
            for m in rx.finditer(text):
                name = m.group(1)
                if name in seen:
                    continue
                seen.add(name)
                out.append({"command": name, "at": f"{path}:{_line(text, m.start())}"})
    return out[:200]


def queues(fc: FactContext) -> list[dict]:
    out, seen = [], set()
    for path in fc.code_files():
        text = fc.text(path)
        for m in QUEUE_RX.finditer(text):
            if m.group(1) not in seen:
                seen.add(m.group(1))
                out.append({"name": m.group(1), "at": f"{path}:{_line(text, m.start())}"})
        for lib in re.findall(r"\b(celery|bullmq|bull|hangfire|sidekiq|rq|dramatiq|MassTransit|NServiceBus|kafkajs|"
                              r"ServiceBusClient|QueueClient|amqplib|pika|aio_pika|sqs)\b", text):
            key = "lib:" + lib.lower()
            if key not in seen:
                seen.add(key)
                out.append({"name": f"({lib})", "at": path})
    return out[:200]


def pipelines(fc: FactContext) -> list[dict]:
    out = []
    for path in fc.code_files():
        text = fc.text(path)
        for m in DAG_RX.finditer(text):
            out.append({"name": m.group(1) or m.group(2) or path, "tool": "airflow", "at": f"{path}:{_line(text, m.start())}"})
    for f in fc.files:
        if f["path"].endswith(".sql") and "/models/" in f["path"]:
            out.append({"name": f["path"].rsplit("/", 1)[-1][:-4], "tool": "dbt", "at": f["path"]})
    return out[:300]


def public_api(fc: FactContext) -> list[dict]:
    rows = fc.ix.db.execute("SELECT path, qualname, kind, signature, doc FROM symbols WHERE parent='' AND "
                            "(exported=1 OR substr(name,1,1) != '_') ORDER BY exported DESC, path LIMIT 600").fetchall()
    return [{"symbol": r["qualname"], "kind": r["kind"], "signature": r["signature"], "at": r["path"], "doc": r["doc"] or ""}
            for r in rows if not TEST_RX.search(r["path"]) and not GENERATED_RX.search(r["path"])][:300]


def integrations(fc: FactContext) -> list[dict]:
    from .testing import SIDE_EFFECT_RX
    hits: dict[str, set] = defaultdict(set)
    sdk_rx = re.compile(r"\b(stripe|twilio|sendgrid|mailgun|smtplib|nodemailer|boto3|aws-sdk|@aws-sdk/[\w-]+|"
                        r"@azure/[\w-]+|azure\.[\w.]+|Azure\.[\w.]+|google\.cloud\.[\w.]+|@google-cloud/[\w-]+|firebase|"
                        r"redis|ioredis|kafka|rabbitmq|amqp|elasticsearch|openai|anthropic|sentry|datadog|"
                        r"newrelic|applicationinsights|opentelemetry|auth0|okta|slack_sdk|@slack/[\w-]+|sentry_sdk|@sentry/[\w-]+|"
                        r"emails|celery|pusher|algolia|cloudinary|segment|mixpanel|posthog|paypal|braintree|adyen|"
                        r"plaid|hubspot|salesforce|zendesk|intercom|launchdarkly|unleash|minio)\b")
    for path in fc.code_files():
        text = fc.text(path)
        for m in sdk_rx.finditer(text):
            hits[m.group(1)].add(path)
        if SIDE_EFFECT_RX["network"].search(text):
            for url in re.findall(r"""['"](https?://[^'"\s{}$]+)""", text)[:5]:
                host = re.sub(r"^https?://([^/]+).*", r"\1", url)
                if not re.search(r"localhost|127\.0\.0\.1|example\.(com|org)|schemas?\.|w3\.org|xmlns", host):
                    hits["http:" + host].add(path)
    return [{"service": k, "used_in": sorted(v)[:5]} for k, v in sorted(hits.items())][:150]


def auth(fc: FactContext) -> list[dict]:
    out = []
    blob_paths = list(fc.code_files()) + [f["path"] for f in fc.files if f["path"].endswith(
        ("package.json", "requirements.txt", "pyproject.toml", "pom.xml", ".csproj", "go.mod"))]
    for label, pat in AUTH_HINTS.items():
        rx = re.compile(pat, re.I)
        where = [p for p in blob_paths if rx.search(fc.text(p))][:5]
        if where:
            out.append({"mechanism": label, "evidence": where})
    return out


def config_files(fc: FactContext) -> list[str]:
    return [f["path"] for f in fc.files if re.search(
        r"(^|/)(appsettings[\w.]*\.json|application[\w-]*\.(ya?ml|properties)|settings\.py|config\.(ts|js|json|ya?ml)|"
        r"\.env\.(example|sample|template)|host\.json|local\.settings\.sample\.json|next\.config\.\w+|vite\.config\.\w+|"
        r"nuxt\.config\.\w+|angular\.json|tsconfig\.json|pyproject\.toml|serverless\.ya?ml|docker-compose\.ya?ml)$",
        f["path"]) and not is_secret(f["path"])][:100]


# ----------------------------------------------------------------------------- archetypes

ARCHETYPE_SIGNALS = {
    "azure-functions": "host.json + Azure Functions SDK / triggers",
    "aws-lambda": "serverless.yml / SAM template / Lambda handlers",
    "backend": "server framework, endpoints or data access",
    "frontend": "UI framework, pages or components",
    "qa-automation": "test framework dominates the repo (Playwright, Cypress, Selenium, WebdriverIO, Appium, Cucumber...)",
    "mobile": "React Native, Flutter, Expo, native iOS/Android",
    "library": "published package without an app entry point",
    "cli": "console entry points / command parsers",
    "infrastructure": "Terraform, Bicep, CloudFormation, Kubernetes, Helm",
    "data-pipeline": "Airflow DAGs, dbt models, Spark jobs",
    "worker": "queue consumers / background job frameworks",
    "microservices": "several independently deployable services",
}


def detect_archetypes(fc: FactContext, stack: dict) -> list[dict]:
    root = fc.root
    names = {f["path"].rsplit("/", 1)[-1] for f in fc.files}
    paths = [f["path"] for f in fc.files]
    blob_deps = json.dumps(stack).lower()
    res: dict[str, str] = {}
    if "host.json" in names and (azure_functions(fc) or any(p.endswith("function.json") for p in paths)):
        res["azure-functions"] = f"host.json and {len(azure_functions(fc))} function definitions"
    if {"serverless.yml", "serverless.yaml"} & names or any(p.endswith(("template.yaml", "template.yml")) and
                                                          "AWS::Serverless" in fc.text(p) for p in paths):
        res["aws-lambda"] = "serverless/SAM configuration"
    eps = endpoints(fc)
    if stack.get("backend") or (eps and "azure-functions" not in res):
        res["backend"] = f"{', '.join(stack.get('backend') or []) or 'HTTP handlers'}; {len(eps)} endpoints"
    if stack.get("frontend"):
        res["frontend"] = ", ".join(stack["frontend"][:4])
    if stack.get("mobile") or "pubspec.yaml" in names or any(p.endswith(("AndroidManifest.xml", ".xcodeproj")) for p in paths):
        res["mobile"] = ", ".join(stack.get("mobile") or ["native project files"])
    t = tests(fc)
    test_files = len(t["files"])
    src_files = sum(1 for _ in fc.code_files())
    auto_tools = [x for x in ("playwright", "cypress", "selenium", "webdriverio", "wdio", "appium", "cucumber",
                              "specflow", "robotframework", "testcafe", "nightwatch", "karate", "rest-assured",
                              "codeceptjs", "behave", "reqnroll") if x in blob_deps or any(x in p.lower() for p in paths)]
    if auto_tools and (test_files >= max(3, src_files * 0.6) or (not stack.get("backend") and not stack.get("frontend"))):
        res["qa-automation"] = f"{', '.join(auto_tools)}; {test_files} test files vs {src_files} source files"
    if any(p.endswith((".tf", ".bicep")) for p in paths) or any("/helm/" in p or p.startswith("helm/") or "Chart.yaml" in p
                                                                 for p in paths):
        res["infrastructure"] = "infrastructure-as-code files"
    if pipelines(fc) or "dbt_project.yml" in names:
        res["data-pipeline"] = "DAGs / dbt models"
    if cli_commands(fc) and ("[project.scripts]" in fc.text("pyproject.toml") or '"bin"' in fc.text("package.json")
                             or "cobra" in blob_deps or "click" in blob_deps or "commander" in blob_deps):
        res["cli"] = f"{len(cli_commands(fc))} commands"
    q = queues(fc)
    if any(x["name"].startswith("(") for x in q):
        res["worker"] = ", ".join(x["name"] for x in q if x["name"].startswith("("))[:80]
    def _is_service(p: dict) -> bool:
        if re.search(r"(^|/)(web|frontend|ui|client|site|docs|e2e|tests?|emails?|packages/[\w-]*ui)(/|$)", p["path"], re.I):
            return False
        if any(m in p["manifests"] for m in ("pom.xml", "go.mod", "build.gradle", "build.gradle.kts")) or \
                any(x.endswith(".csproj") for x in p["manifests"]):
            return True
        txt = (fc.text(p["path"] + "/package.json") + fc.text(p["path"] + "/requirements.txt")
               + fc.text(p["path"] + "/pyproject.toml")).lower()
        return bool(re.search(r"express|@nestjs|fastify|koa|hono|fastapi|django|flask|aiohttp|starlette", txt))
    backend_projects = [p for p in stack.get("projects", []) if _is_service(p)]
    if len(backend_projects) >= 3 and "backend" in res:
        res["microservices"] = f"{len(backend_projects)} services"
    if not res or (set(res) <= {"cli"} and (root / "pyproject.toml").exists() or (root / "package.json").exists()
                   and not res):
        res.setdefault("library", "package without an app framework")
    return [{"archetype": k, "evidence": v, "meaning": ARCHETYPE_SIGNALS[k]} for k, v in res.items()]


# ----------------------------------------------------------------------------- registry

EXTRACTORS = {
    "endpoints": endpoints, "env_vars": env_vars, "azure_functions": azure_functions, "host_config": host_config,
    "lambda_functions": lambda_functions, "routes": routes, "components": components, "tests": tests,
    "page_objects": page_objects, "models": models, "migrations": migrations, "infra_resources": infra_resources,
    "ci": ci, "cli_commands": cli_commands, "queues": queues, "pipelines": pipelines, "public_api": public_api,
    "integrations": integrations, "auth": auth, "config_files": config_files,
}


def static_facts(fc: FactContext, stack: dict) -> dict:
    return {"stack": {k: stack.get(k) for k in ("languages", "frontend", "mobile", "backend", "orm", "databases",
                                                "test_frameworks", "ci", "ticket_systems")},
            "commands": stack.get("commands"), "projects": stack.get("projects"),
            "databases": stack.get("databases"),
            "modules": [{"module": m["m"], "files": m["files"], "lines": m["lines"]} for m in fc.ix.modules()[:40]],
            "conventions": analyze_conventions(fc.ix)}


def coverage_keys(fact: str, value) -> list[str]:
    """Strings that must appear in a document for each item of a coverage fact."""
    if fact == "endpoints":
        return sorted({e["path"] for e in value})
    if fact == "env_vars":
        return [e["name"] for e in value]
    if fact == "azure_functions" or fact == "lambda_functions":
        return sorted({f["name"] for f in value if f.get("name")})
    if fact == "routes":
        return sorted({r["route"] for r in value})
    if fact == "components":
        return sorted({c["name"] for c in value if c["shared"]} or {c["name"] for c in value})
    if fact == "test_files":
        return [t["file"] for t in value["files"]] if isinstance(value, dict) else []
    if fact == "models":
        return sorted({m["name"] for m in value})
    if fact == "infra_resources":
        return sorted({r["name"] for r in value if r["type"] != "container image"})
    if fact == "public_api":
        return sorted({p["symbol"] for p in value if p["kind"] in ("class", "function", "interface")})[:120]
    if fact == "cli_commands":
        return [c["command"] for c in value]
    if fact == "pipelines":
        return sorted({p["name"] for p in value})
    if fact == "projects":
        return [p["path"] for p in value or []]
    return []


class Facts:
    """Lazily computed, cached facts for one repo state."""

    def __init__(self, ix: CodeIndex):
        self.ix = ix
        self.fc = FactContext(ix)
        self._stack = None
        self._cache: dict[str, object] = {}

    @property
    def stack(self) -> dict:
        if self._stack is None:
            self._stack = detect_stack(self.ix.root, self.ix)
        return self._stack

    def get(self, name: str):
        if name not in self._cache:
            if name == "test_files":
                self._cache[name] = self.get("tests")
            elif name in EXTRACTORS:
                self._cache[name] = EXTRACTORS[name](self.fc)
            elif name in ("stack", "commands", "projects", "databases", "modules", "conventions"):
                self._cache[name] = static_facts(self.fc, self.stack)[name]
            elif name == "doc_list":
                self._cache[name] = []
            else:
                raise KeyError(name)
        return self._cache[name]

    def archetypes(self) -> list[dict]:
        if "_arch" not in self._cache:
            self._cache["_arch"] = detect_archetypes(self.fc, self.stack)
        return self._cache["_arch"]

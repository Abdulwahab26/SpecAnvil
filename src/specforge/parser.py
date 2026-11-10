"""Language-agnostic symbol extraction.

Primary: tree-sitter-language-pack (300+ grammars, downloaded on first use and cached).
Fallback: regex heuristics for when a grammar is unavailable (offline, unknown language).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath


try:  # pragma: no cover - import guard
    import tree_sitter_language_pack as tslp
except Exception:  # noqa: BLE001
    tslp = None

_FAILED_LANGS: set[str] = set()

# Languages worth extracting symbols from. Everything else (docs, data, config) is indexed as text.
CODE_LANGUAGES = {
    "python", "javascript", "typescript", "tsx", "java", "kotlin", "csharp", "go", "rust", "ruby", "php",
    "swift", "scala", "dart", "c", "cpp", "objc", "elixir", "erlang", "haskell", "lua", "r", "julia",
    "perl", "fsharp", "clojure", "ocaml", "zig", "vue", "svelte", "groovy", "solidity", "elm", "nim",
    "crystal", "gleam", "apex", "cobol", "fortran", "pascal", "vb", "matlab", "powershell", "hack",
}

# Grammar policy: indexing NEVER downloads anything. Grammars are fetched only by the explicit
# `specforge grammars` step (which `specforge init` runs unless --offline). Files whose grammar
# is not cached locally fall back to the regex extractor, which is pure Python.
_LOCAL_GRAMMARS: set[str] | None = None


def local_grammars(refresh: bool = False) -> set[str]:
    global _LOCAL_GRAMMARS
    if _LOCAL_GRAMMARS is None or refresh:
        try:
            _LOCAL_GRAMMARS = set(tslp.downloaded_languages()) if tslp is not None else set()
        except Exception:  # noqa: BLE001
            _LOCAL_GRAMMARS = set()
    return _LOCAL_GRAMMARS


def fetch_grammars(langs: list[str]) -> dict:
    """Explicit, one-time network step: download tree-sitter grammars into the local cache."""
    if tslp is None:
        return {"downloaded": [], "unavailable": sorted(langs), "cache": None}
    wanted = sorted({l for l in langs if l in CODE_LANGUAGES} - local_grammars(refresh=True))
    ok, bad = [], []
    for lang in wanted:
        try:
            tslp.download([lang])
            ok.append(lang)
        except Exception:  # noqa: BLE001 - not in manifest, offline, etc.
            bad.append(lang)
    local_grammars(refresh=True)
    try:
        cache = str(tslp.cache_dir())
    except Exception:  # noqa: BLE001
        cache = None
    return {"downloaded": ok, "unavailable": bad, "already_local": sorted(set(langs) & local_grammars()),
            "cache": cache}
PARSER_VERSION = "8"  # bump to force a full re-index after parser changes

_EXT_FALLBACK = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "javascript", ".ts": "typescript", ".tsx": "tsx", ".java": "java",
    ".kt": "kotlin", ".kts": "kotlin", ".cs": "csharp", ".go": "go", ".rb": "ruby",
    ".php": "php", ".rs": "rust", ".swift": "swift", ".dart": "dart", ".scala": "scala",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp", ".vue": "vue",
    ".svelte": "svelte", ".sql": "sql", ".md": "markdown", ".json": "json",
    ".yml": "yaml", ".yaml": "yaml", ".toml": "toml", ".xml": "xml", ".html": "html",
    ".css": "css", ".scss": "scss", ".sh": "bash", ".ps1": "powershell",
    ".graphql": "graphql", ".gql": "graphql", ".proto": "proto", ".tf": "hcl",
    ".ex": "elixir", ".exs": "elixir", ".lua": "lua", ".r": "r", ".m": "objc",
    ".fs": "fsharp", ".clj": "clojure", ".hs": "haskell", ".erl": "erlang",
}


@dataclass
class Symbol:
    name: str
    kind: str
    qualname: str
    signature: str
    start_line: int  # 1-based inclusive
    end_line: int    # 1-based inclusive
    doc: str = ""
    parent: str = ""
    exported: bool = False


@dataclass
class FileParse:
    language: str
    parser: str  # "tree-sitter" | "regex" | "text"
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    calls: list = field(default_factory=list)     # (caller_qualname, callee_name, line)
    bases: list = field(default_factory=list)     # (class_name, base_name)
    package: str = ""
    idents: dict = field(default_factory=dict)    # identifier -> occurrences


def detect_language(rel_path: str) -> str:
    name = PurePosixPath(rel_path).name.lower()
    if name == "dockerfile" or name.startswith("dockerfile."):
        return "dockerfile"
    if name in ("makefile", "gnumakefile"):
        return "make"
    if (name.startswith(".") and name.count(".") == 1) or name.endswith((".txt", ".lock", ".log")):
        return "requirements" if name.startswith("requirements") else "text"
    if tslp is not None:
        try:
            lang = tslp.detect_language_from_path(rel_path)
            if lang:
                return str(lang)
        except Exception:  # noqa: BLE001
            pass
    return _EXT_FALLBACK.get(PurePosixPath(rel_path).suffix.lower(), "text")


def _first_line(text: str | None, limit: int = 160) -> str:
    if not text:
        return ""
    for line in text.strip().strip('"\'/*#').splitlines():
        line = line.strip().strip('"\'/*#').strip()
        if line:
            return line[:limit]
    return ""


def _kind(k) -> str:
    s = str(getattr(k, "name", k)).lower().rstrip("_")
    return {"class_": "class"}.get(s, s)


def _parse_tree_sitter(source: str, lang: str) -> FileParse | None:
    if tslp is None or lang in _FAILED_LANGS or lang not in local_grammars():
        return None  # never trigger a download from inside indexing
    try:
        res = tslp.process(source, tslp.ProcessConfig(language=lang, structure=True,
                                                      imports=True, exports=True, docstrings=True))
    except Exception:  # noqa: BLE001 - missing grammar, offline, parse failure
        _FAILED_LANGS.add(lang)
        return None

    docs: dict[str, str] = {}
    for d in (res.docstrings or []):
        if getattr(d, "associated_item", None):
            docs.setdefault(d.associated_item, _first_line(d.text))
    exported_names = set()
    for e in (res.exports or []):
        nm = getattr(e, "name", None)
        if nm:
            exported_names.add(nm)

    symbols: list[Symbol] = []

    def walk(items, parent: str):
        for it in items:
            if not it.name:
                walk(it.children or [], parent)
                continue
            kind = _kind(it.kind)
            if kind == "module" and not it.children:
                continue
            qual = f"{parent}.{it.name}" if parent else it.name
            sig_lines = [ln.strip() for ln in (it.signature or it.name).strip().splitlines()]
            body = [ln for ln in sig_lines if ln and not ln.startswith("@")] or sig_lines or [it.name]
            sig = " ".join(body)[:240]
            symbols.append(Symbol(
                name=it.name, kind=kind, qualname=qual, signature=sig,
                start_line=it.span.start_line + 1, end_line=it.span.end_line + 1,
                doc=_first_line(it.doc_comment) or docs.get(it.name, ""),
                parent=parent, exported=it.name in exported_names or sig.startswith("export"),
            ))
            walk(it.children or [], qual)

    walk(res.structure or [], "")
    imports = []
    for imp in (res.imports or []):
        src = (getattr(imp, "source", "") or "").strip()
        if src:
            imports.append(src.splitlines()[0][:200])
    return FileParse(language=lang, parser="tree-sitter", symbols=symbols, imports=imports)


# --- regex fallback ---------------------------------------------------------

_RX = [
    ("class", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:public\s+|private\s+|protected\s+|internal\s+)?"
                         r"(?:abstract\s+|final\s+|sealed\s+|static\s+|partial\s+|data\s+|open\s+)*"
                         r"(class|interface|struct|enum|trait|record|object|protocol|type)\s+([A-Za-z_]\w*)")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:pub(?:\(\w+\))?\s+)?"
                            r"(def|func|fn|function|fun|sub|proc)\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?"
                            r"(?:\([^)]*\)|[A-Za-z_]\w*)\s*(?::[^=]+)?=>")),
    ("method", re.compile(r"^\s+(?:@\w+(?:\([^)]*\))?\s+)*(?:public|private|protected|internal)\s+"
                          r"(?:static\s+|async\s+|override\s+|virtual\s+|final\s+|abstract\s+)*"
                          r"[\w<>\[\],?. ]+\s+([A-Za-z_]\w*)\s*\(")),
]
_IMPORT_RX = re.compile(r"^\s*(?:import\s.+|from\s+\S+\s+import\s.+|using\s+[\w.]+;|"
                        r"#include\s+\S+|require\(.+\)|use\s+[\w:\\]+;?)", re.M)


def _parse_regex(source: str, lang: str) -> FileParse:
    lines = source.splitlines()
    found: list[tuple[int, str, str, int]] = []  # line, kind, name, indent
    for i, line in enumerate(lines):
        for kind, rx in _RX:
            m = rx.match(line)
            if m:
                name = m.group(m.lastindex) if m.lastindex else m.group(1)
                if kind == "class" and m.lastindex and m.lastindex >= 2:
                    k = m.group(1)
                    kind = "interface" if k in ("interface", "protocol", "trait") else (
                        "enum" if k == "enum" else "struct" if k == "struct" else "class")
                found.append((i, kind, name, len(line) - len(line.lstrip())))
                break
    symbols: list[Symbol] = []
    stack: list[tuple[int, str]] = []  # indent, qualname
    for idx, (i, kind, name, indent) in enumerate(found):
        # end line: next symbol at same or lower indent, else EOF
        end = len(lines)
        for j, _, _, ind2 in found[idx + 1:]:
            if ind2 <= indent:
                end = j
                break
        while end - 1 > i and not lines[end - 1].strip():
            end -= 1
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else ""
        qual = f"{parent}.{name}" if parent else name
        if kind == "function" and parent:
            kind = "method"
        doc = ""
        if i > 0 and lines[i - 1].strip().startswith(("//", "#", "*", "/**", "///")):
            doc = _first_line(lines[i - 1])
        symbols.append(Symbol(name=name, kind=kind, qualname=qual, signature=lines[i].strip()[:240],
                              start_line=i + 1, end_line=max(i + 1, end), doc=doc, parent=parent,
                              exported=lines[i].lstrip().startswith(("export", "public", "pub "))))
        if kind in ("class", "interface", "struct", "enum"):
            stack.append((indent, qual))
    imports = [m.group(0).strip()[:200] for m in _IMPORT_RX.finditer(source)][:200]
    return FileParse(language=lang, parser="regex", symbols=symbols, imports=imports)


# CommonJS / prototype-style definitions that tree-sitter's structure view does not report:
#   Layer.prototype.handle_request = function (...) {   proto.handle = function handle(...) {
#   exports.render = function (...) {                   module.exports = function createApp() {
_JS_ASSIGN_RX = re.compile(
    r"^([ \t]*)((?:[A-Za-z_$][\w$]*\.)*(?:prototype\.)?[A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?function\b[^(]*\(", re.M)


def _block_end(source: str, start: int) -> int | None:
    """Index of the brace closing the first '{' at/after start (string- and comment-aware enough for JS)."""
    i = source.find("{", start)
    if i < 0:
        return None
    depth, n, quote = 0, len(source), None
    while i < n:
        c = source[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'`":
            quote = c
        elif c == "/" and source.startswith("//", i):
            j = source.find("\n", i)
            i = n if j < 0 else j
            continue
        elif c == "/" and source.startswith("/*", i):
            j = source.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _js_assignments(source: str, existing: list[Symbol]) -> list[Symbol]:
    taken = {s.start_line for s in existing}
    out = []
    for m in _JS_ASSIGN_RX.finditer(source):
        target = m.group(2)
        parts = [p for p in target.split(".") if p not in ("prototype",)]
        if parts[0] in ("module",) and len(parts) <= 2 and parts[-1] == "exports":
            parts = ["module.exports"]
        line = source.count("\n", 0, m.start()) + 1
        if line in taken:
            continue
        end = _block_end(source, m.end())
        end_line = source.count("\n", 0, end) + 1 if end is not None else line
        name = parts[-1]
        parent = ".".join(parts[:-1]) if len(parts) > 1 and parts[0] not in ("exports", "module.exports") else ""
        if parent in ("proto", "app", "res", "req", "exports", "module"):
            parent = {"proto": "", "exports": "", "module": ""}.get(parent, parent)
        sig = source[m.start():source.find("{", m.end()) if source.find("{", m.end()) > 0 else m.end()].strip()
        out.append(Symbol(name=name, kind="method" if parent or ".prototype." in target else "function",
                          qualname=f"{parent}.{name}" if parent else name, signature=sig[:240],
                          start_line=line, end_line=max(line, end_line), parent=parent, exported=target.startswith(("exports.", "module.exports"))))
    return out


def parse_source(rel_path: str, source: str) -> FileParse:
    fp = _parse_symbols(rel_path, source)
    if fp.parser == "text":
        from .configkeys import extract_keys, extract_sections
        for title, crumb, level, start, end in extract_sections(rel_path, fp.language, source):
            parent = crumb.rsplit(" > ", 1)[0] if " > " in crumb else ""
            fp.symbols.append(Symbol(name=title, kind="section", qualname=crumb, signature="#" * level + " " + title,
                                     start_line=start, end_line=end, parent=parent))
        for key, line, value in extract_keys(rel_path, fp.language, source):
            name = key.rsplit(".", 1)[-1]
            fp.symbols.append(Symbol(name=name, kind="key", qualname=key,
                                     signature=f"{key}: {value}" if value else key, start_line=line, end_line=line,
                                     parent=key.rsplit(".", 1)[0] if "." in key else ""))
        return fp
    from .extract import extract
    if fp.language in ("javascript", "typescript", "tsx") and "require(" in source:
        known = set(fp.imports)
        for m in re.finditer(r"""require\(\s*['"]([^'"]+)['"]\s*\)""", source):
            stmt = f"require('{m.group(1)}')"
            if stmt not in known:
                fp.imports.append(stmt)
                known.add(stmt)
    ex = extract(source, fp.language, fp.symbols)
    for name, kind, line, parent in ex.decls:
        fp.symbols.append(Symbol(name=name, kind=kind, qualname=f"{parent}.{name}" if parent else name,
                                 signature=source.split("\n")[line - 1].strip()[:200], start_line=line,
                                 end_line=line, parent=parent,
                                 exported=source.split("\n")[line - 1].lstrip().startswith(("export", "pub ", "public"))))
    fp.symbols.sort(key=lambda s: (s.start_line, s.end_line))
    fp.calls, fp.bases, fp.package, fp.idents = ex.calls, ex.bases, ex.package, ex.idents
    return fp


def _parse_symbols(rel_path: str, source: str) -> FileParse:
    lang = detect_language(rel_path)
    if lang not in CODE_LANGUAGES:
        return FileParse(language=lang, parser="text")
    parsed = _parse_tree_sitter(source, lang)
    if parsed is not None and lang in ("javascript", "typescript", "tsx") and "= function" in source:
        parsed.symbols += _js_assignments(source, parsed.symbols)
        parsed.symbols.sort(key=lambda x: x.start_line)
    if parsed is not None and (parsed.symbols or len(source) < 200):
        return parsed
    fallback = _parse_regex(source, lang)
    if parsed is not None and not fallback.symbols:
        return parsed
    return fallback

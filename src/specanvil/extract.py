"""Second extraction pass per file: declarations, call sites, inheritance, package and identifiers.

Runs after symbol parsing (tree-sitter or regex) on the same source text, in the same worker
process, so it adds no extra file reads. Everything is regex over source with the symbol ranges
already known, which keeps it language-agnostic and fast (see docs/BENCHMARKS.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

KEYWORDS = set("""
if else elif for while do switch case break continue return yield await async def function fn func class
struct interface enum trait impl type new delete try catch except finally raise throw throws with as from
import export default package namespace using public private protected internal static final const let var
val readonly abstract override virtual sealed open lambda in is not and or of typeof instanceof sizeof void
null nil none true false self this super base print println printf echo require include module end then
begin rescue ensure unless until when match pass global nonlocal assert del go defer chan select map range
string int float bool char byte long short double decimal object any unknown never extends implements where
get set""".split())

REF_RX = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]{2,})\b(?!\s*[(=:]|\s*<[^<>()]{0,60}>\s*\()")
CALL_RX = re.compile(r"(?:([A-Za-z_$][\w$]*)\s*(?:\.|::|->)\s*)?(?<![\w$])(?:new\s+)?([A-Za-z_$][\w$]*)\s*(?:<[^<>()]{0,60}>)?\s*\(")
IDENT_RX = re.compile(r"[A-Za-z_$][\w$]{2,}")
STRING_RX = re.compile(r"""("(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`[^`]*`)""")
COMMENT_RX = re.compile(r"(?m)(?:^|\s)(?://|#(?![\[!]))[^\n]*$|/\*.*?\*/", re.S)

PACKAGE_RX = re.compile(r"(?m)^\s*(?:package|namespace)\s+([\w.\\]+)")

BASE_PATTERNS = [
    re.compile(r"\bclass\s+(\w+)\s*(?:<[^>]*>)?\s*\(([^)]*)\)\s*:"),                         # python
    re.compile(r"\b(?:class|interface)\s+(\w+)(?:<[^{]*?>)?\s+extends\s+([\w.<>, ]+?)(?:\s+implements\s+([\w.<>, ]+?))?\s*\{"),
    re.compile(r"\bclass\s+(\w+)(?:<[^{]*?>)?\s+implements\s+([\w.<>, ]+?)\s*\{"),
    re.compile(r"\b(?:class|struct|interface|record|object)\s+(\w+)(?:<[^>{]*>)?(?:\([^)]*\))?\s*:\s*([\w.<>, ()]+?)\s*(?:\{|where|$)", re.M),  # C#/Kotlin/Swift
    re.compile(r"\bclass\s+(\w+)\s*<\s*([\w:]+)"),                                           # ruby
    re.compile(r"\bimpl(?:<[^>]*>)?\s+([\w:]+)(?:<[^>]*>)?\s+for\s+(\w+)"),                  # rust (trait, type) reversed
]

DECL_PATTERNS: dict[str, list[tuple[str, re.Pattern]]] = {
    "python": [
        ("auto", re.compile(r"^([A-Za-z_]\w*)[ \t]*(?::[^=\n]*)?=(?!=)", re.M)),
        ("type", re.compile(r"^type\s+(\w+)\s*(?:\[[^\]]*\])?\s*=", re.M)),
        ("field", re.compile(r"^[ \t]+([A-Za-z_]\w*)[ \t]*(?::[^=\n]*)?=(?!=)", re.M)),
        ("field", re.compile(r"^[ \t]+([A-Za-z_]\w*)[ \t]*:[ \t]*[A-Za-z_][\w\[\]|., \"']*$", re.M)),  # annotated, no default
    ],
    "js": [
        ("auto", re.compile(r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=\n]+)?=(?!\s*(?:async\s*)?(?:\([^)]*\)|[\w$]+)\s*(?::[^=]+)?=>|\s*(?:async\s+)?function\b)", re.M)),
        ("type", re.compile(r"^(?:export\s+)?(?:declare\s+)?type\s+(\w+)\s*(?:<[^=]*>)?\s*=", re.M)),
        ("enum", re.compile(r"^(?:export\s+)?(?:declare\s+)?(?:const\s+)?enum\s+(\w+)", re.M)),
        ("field", re.compile(r"^[ \t]+(?:(?:public|private|protected|readonly|static|declare|override)\s+)*#?([A-Za-z_$][\w$]*)\s*[?!]?\s*(?::\s*[^=;\n(]+?)?\s*(?:=(?!>)[^;\n]*)?;?\s*$", re.M)),
    ],
    "go": [
        ("constant", re.compile(r"^const\s+(\w+)", re.M)),
        ("variable", re.compile(r"^var\s+(\w+)", re.M)),
        ("type", re.compile(r"^type\s+(\w+)\s+(?!struct|interface)", re.M)),
        ("block", re.compile(r"^(const|var)\s*\(\s*\n(.*?)^\)", re.M | re.S)),
        ("field", re.compile(r"^[ \t]+([A-Za-z_]\w*)[ \t]+[*\[\]\w.{}]+(?:[ \t]+`[^`\n]*`)?[ \t]*$", re.M)),  # struct fields
    ],
    "jvm": [  # java, kotlin, csharp, scala, dart
        ("field", re.compile(r"^[ \t]+(?:(?:public|private|protected|internal|static|final|readonly|const|volatile|transient|lateinit|override)\s+)+(?:val\s+|var\s+)?[\w<>\[\],.?]+(?:\s+|\s*:\s*)?([A-Za-z_]\w*)\s*(?::\s*[\w<>?,. ]+)?\s*(?:=|;|$)", re.M)),
        ("auto", re.compile(r"^(?:(?:private|internal|public|const)\s+)*(?:val|var)\s+([A-Za-z_]\w*)", re.M)),  # kotlin top-level
    ],
    "kotlin": [  # `private lateinit var owners: OwnerRepository`: the name comes before the type
        ("field", re.compile(r"^[ \t]+(?:@[\w.:]+(?:\([^)\n]*\))?[ \t]+)*(?:(?:public|private|protected|internal|open|override|"
                             r"abstract|final|const|lateinit)[ \t]+)*(?:val|var)[ \t]+(?:[\w<>?, ]+\.)?([A-Za-z_]\w*)", re.M)),
        ("auto", re.compile(r"^(?:(?:private|internal|public|const|lateinit)[ \t]+)*(?:val|var)[ \t]+([A-Za-z_]\w*)", re.M)),
    ],
    "rust": [
        ("constant", re.compile(r"^\s*(?:pub(?:\([\w:]+\))?\s+)?(?:const|static)\s+(?:mut\s+)?(\w+)", re.M)),
        ("type", re.compile(r"^\s*(?:pub(?:\([\w:]+\))?\s+)?type\s+(\w+)", re.M)),
    ],
    "ruby": [("constant", re.compile(r"^\s*([A-Z][A-Z0-9_]+)\s*=", re.M))],
    "php": [("constant", re.compile(r"^\s*(?:(?:public|private|protected)\s+)?const\s+(\w+)", re.M)),
            ("field", re.compile(r"^\s+(?:public|private|protected)(?:\s+static)?(?:\s+\??[\w\\]+)?\s+\$(\w+)", re.M))],
    "c": [("constant", re.compile(r"^#define\s+([A-Z_][A-Z0-9_]*)", re.M)),
          ("type", re.compile(r"^typedef\s+[^;]*?\b(\w+)\s*;", re.M))],
}
FAMILY = {"python": "python", "javascript": "js", "typescript": "js", "tsx": "js", "vue": "js", "svelte": "js",
          "go": "go", "java": "jvm", "kotlin": "kotlin", "csharp": "jvm", "scala": "jvm", "dart": "jvm",
          "rust": "rust", "ruby": "ruby", "php": "php", "c": "c", "cpp": "c", "objc": "c"}


@dataclass
class Extras:
    decls: list = field(default_factory=list)      # (name, kind, line, parent)
    calls: list = field(default_factory=list)      # (caller_qualname, callee, line, receiver)
    bases: list = field(default_factory=list)      # (class_name, base_name)
    package: str = ""
    idents: dict = field(default_factory=dict)     # unused (kept for pickling compatibility)
    new_symbols: list = field(default_factory=list)  # container symbols discovered here (e.g. Go structs)


INTERP_RX = re.compile(r"\{([^{}\n]*)\}")


def _strip_noise(source: str) -> str:
    """Blank out strings and comments, keeping line structure, so calls/idents come from code only."""
    def blank(m):
        text = m.group(0)
        out = re.sub(r"[^\n]", " ", text)
        # interpolated strings contain real code: f"{slug(x)}", `${fmt(x)}`, "#{name}", $"{Total()}"
        prev = source[m.start() - 1] if m.start() else ""
        if "{" in text and (text[0] == "`" or prev in "fF$" or text[:1] in "fF" or "${" in text or "#{" in text):
            chars = list(out)
            for seg in INTERP_RX.finditer(text):
                chars[seg.start(1):seg.end(1)] = text[seg.start(1):seg.end(1)]
            out = "".join(chars)
        return out
    s = STRING_RX.sub(blank, source)
    return COMMENT_RX.sub(blank, s)


def _clean_base(b: str) -> str:
    b = re.sub(r"<[^>]*>|\([^)]*\)", "", b).strip()
    b = b.split("=")[0].strip()
    return b.rsplit(".", 1)[-1].rsplit("::", 1)[-1]


GO_TYPE_RX = re.compile(r"^type\s+(\w+)(?:\[[^\]]*\])?\s+(struct|interface)\s*\{", re.M)
GO_RECV_RX = re.compile(r"^func\s*\(\s*\w*\s*\*?\s*(\w+)(?:\[[^\]]*\])?\s*\)\s*(\w+)")


def _go_types_and_receivers(code: str, symbols: list, line_of, ex) -> None:
    """Go: struct/interface types (not in tree-sitter's structure view) and receiver-qualified methods."""
    from .parser import Symbol, _block_end
    have = {(s.name, s.start_line) for s in symbols}
    for m in GO_TYPE_RX.finditer(code):
        line = line_of(m.start())
        if (m.group(1), line) in have:
            continue
        end = _block_end(code, m.end() - 1)
        end_line = line_of(end) if end is not None else line
        symbols.append(Symbol(name=m.group(1), kind=m.group(2), qualname=m.group(1),
                              signature=code[m.start():m.end()].strip(), start_line=line, end_line=end_line,
                              exported=m.group(1)[:1].isupper()))
        ex.new_symbols.append(symbols[-1])
    for s in symbols:
        if s.kind in ("method", "function") and not s.parent:
            r = GO_RECV_RX.match(s.signature)
            if r and r.group(2) == s.name:
                s.parent, s.qualname, s.kind = r.group(1), f"{r.group(1)}.{s.name}", "method"


def extract(source: str, lang: str, symbols: list) -> Extras:
    ex = Extras()
    code = _strip_noise(source)
    lines = code.split("\n")
    line_starts = [0]
    for ln in lines:
        line_starts.append(line_starts[-1] + len(ln) + 1)

    def line_of(pos: int) -> int:
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    if lang == "go":
        _go_types_and_receivers(code, symbols, line_of, ex)
    containers = [s for s in symbols if s.kind in ("class", "interface", "struct", "trait", "impl", "enum", "record",
                                                    "object", "namespace", "module")]
    callables = [s for s in symbols if s.kind in ("function", "method")]

    n_lines = len(lines) + 2

    def innermost_table(pool) -> list:
        """line -> innermost symbol in pool, built in O(total range) instead of O(matches x symbols)."""
        table: list = [None] * n_lines
        for s in sorted(pool, key=lambda x: x.start_line - x.end_line):  # largest first, smaller overwrite
            a, b = max(1, s.start_line), min(n_lines - 1, s.end_line)
            if b >= a:
                table[a:b + 1] = [s] * (b - a + 1)
        return table

    callable_at = innermost_table(callables)
    container_at = innermost_table(containers)

    def enclosing(line: int, pool) -> Any:
        t = callable_at if pool is callables else container_at
        return t[line] if 0 <= line < n_lines else None

    # package / namespace
    m = PACKAGE_RX.search(code)
    if m:
        ex.package = m.group(1).replace("\\", ".")

    # declarations
    fam = FAMILY.get(lang)
    taken = {(s.name, s.start_line) for s in symbols}
    if fam:
        for kind, rx in DECL_PATTERNS.get(fam, []):
            for m in rx.finditer(code):
                if kind == "block":  # go const/var blocks
                    base = line_of(m.start(2))
                    for i, ln in enumerate(m.group(2).split("\n")):
                        mm = re.match(r"\s*([A-Za-z_]\w*)\b", ln)
                        if mm and mm.group(1) not in KEYWORDS:
                            ex.decls.append((mm.group(1), "constant" if m.group(1) == "const" else "variable", base + i, ""))
                    continue
                name = m.group(1)
                if name in KEYWORDS or name.startswith("__") and name.endswith("__") and name != "__all__":
                    continue
                line = line_of(m.start(1))
                if (name, line) in taken:
                    continue
                if enclosing(line, callables) is not None:
                    continue  # locals are not symbols
                parent_sym = enclosing(line, containers)
                parent = parent_sym.qualname if parent_sym else ""
                k = kind
                if k == "auto":
                    if parent and fam in ("python", "js"):
                        k = "field"
                    elif re.fullmatch(r"[A-Z][A-Z0-9_]+", name):
                        k = "constant"
                    else:
                        k = "variable"
                elif k == "field":
                    if not parent:
                        continue
                    head = lines[line - 1]
                    if re.search(r"\b(static\s+final|final\s+static|const)\b", head) or re.fullmatch(r"[A-Z][A-Z0-9_]+", name):
                        k = "constant"
                elif k == "constant" and fam == "ruby" and parent:
                    k = "constant"
                ex.decls.append((name, k, line, parent))
                taken.add((name, line))

    # inheritance
    for rx in BASE_PATTERNS:
        for m in rx.finditer(code):
            if rx is BASE_PATTERNS[-1]:  # rust: impl Trait for Type
                ex.bases.append((m.group(2), _clean_base(m.group(1))))
                continue
            cls = m.group(1)
            groups = [g for g in m.groups()[1:] if g]
            for g in groups:
                for b in re.split(r",(?![^<]*>)", g):
                    b = _clean_base(b)
                    if b and b not in ("object", "Object", "metaclass", "ABC") and b[0].isalpha() and b not in KEYWORDS:
                        ex.bases.append((cls, b))

    # calls: one linear pass, each call attributed to the innermost enclosing function/method
    seen_by: dict[int, set] = {}
    local_funcs = {x.name for x in symbols if x.kind in ("function", "method") and len(x.name) >= 3}
    for ln_no in range(1, len(lines) + 1):
        owner = callable_at[ln_no] if ln_no < n_lines else None
        if owner is None:
            continue
        text = lines[ln_no - 1]
        if ln_no == owner.start_line:  # skip the definition itself: only code after its parameter list
            at = text.find(owner.name + "(")
            start = at + len(owner.name) if at >= 0 else text.find("(")
            if start < 0:
                continue
            depth, close = 0, -1
            for j in range(start, len(text)):
                if text[j] == "(":
                    depth += 1
                elif text[j] == ")":
                    depth -= 1
                    if depth == 0:
                        close = j
                        break
            if close < 0:
                continue
            text = text[close + 1:]
        seen = seen_by.setdefault(id(owner), set())
        if len(seen) > 200:
            continue
        # functions passed as values (callbacks, handler tables, set_defaults(fn=handler)): record a reference
        # edge to functions defined in this file; "&" marks it so the resolver stays inside the file
        if local_funcs and ln_no != owner.start_line:
            for rm in REF_RX.finditer(text):
                name = rm.group(1)
                if name in local_funcs and name != owner.name and ("&", name) not in seen:
                    seen.add(("&", name))
                    ex.calls.append((owner.qualname, name, ln_no, "&"))
        if "(" not in text:
            continue
        for cm in CALL_RX.finditer(text):
            recv, callee = cm.group(1) or "", cm.group(2)
            # `repo.delete(...)` is a method call even though `delete` is a keyword; a bare `delete (...)` is not
            if (callee in KEYWORDS and not recv) or len(callee) < 2 or (recv, callee) in seen:
                continue
            seen.add((recv, callee))
            ex.calls.append((owner.qualname, callee, ln_no, recv if recv not in KEYWORDS else ""))

    return ex

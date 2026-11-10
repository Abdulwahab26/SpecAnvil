"""Structural (AST) search with tree-sitter queries, plus anti-pattern presets.

The query language is tree-sitter's standard S-expression pattern language
(https://tree-sitter.github.io/tree-sitter/using-parsers/queries), so anything you can express
there works here, with predicates like #eq? / #match?. Each result is labelled with its enclosing
symbol. Presets cover common review findings in the major languages.
"""

from __future__ import annotations

import fnmatch

from .index import GENERATED_RX, CodeIndex
from .parser import local_grammars

try:  # pragma: no cover
    import tree_sitter as ts
    import tree_sitter_language_pack as tslp
except Exception:  # noqa: BLE001
    ts = tslp = None

JS = ("javascript", "typescript", "tsx")
PRESETS: dict[str, dict] = {
    "empty-catch": {
        "why": "Swallowed errors hide failures.",
        "python": "(except_clause (block . (pass_statement) .)) @hit",
        **{l: '(catch_clause body: (statement_block) @b (#match? @b "^\\\\{\\\\s*\\\\}$")) @hit' for l in JS},
        "java": '(catch_clause body: (block) @b (#match? @b "^\\\\{\\\\s*\\\\}$")) @hit',
        "csharp": '(catch_clause body: (block) @b (#match? @b "^\\\\{\\\\s*\\\\}$")) @hit',
    },
    "broad-except": {
        "why": "Catching everything masks bugs; catch specific exceptions.",
        "python": '[(except_clause !value) (except_clause value: (identifier) @t (#match? @t "^(Exception|BaseException)$"))] @hit',
        "java": '(catch_formal_parameter (catch_type) @t (#match? @t "^(Exception|Throwable)$")) @hit',
        "csharp": '(catch_declaration type: (identifier) @t (#eq? @t "Exception")) @hit',
    },
    "debug-print": {
        "why": "Leftover debugging output in production code.",
        "python": '((call function: (identifier) @f) (#eq? @f "print")) @hit',
        **{l: '((call_expression function: (member_expression object: (identifier) @o property: (property_identifier) @p)) '
              '(#eq? @o "console") (#match? @p "^(log|debug|trace)$")) @hit' for l in JS},
        "java": '((method_invocation object: (field_access) @o name: (identifier) @n) (#match? @o "System\\\\.(out|err)") (#match? @n "^print")) @hit',
        "go": '((call_expression function: (selector_expression operand: (identifier) @o field: (field_identifier) @f)) (#eq? @o "fmt") (#match? @f "^Print")) @hit',
    },
    "eval-exec": {
        "why": "Dynamic code execution is an injection risk.",
        "python": '((call function: (identifier) @f) (#match? @f "^(eval|exec)$")) @hit',
        **{l: '[((call_expression function: (identifier) @f) (#eq? @f "eval")) (new_expression constructor: (identifier) @c (#eq? @c "Function"))] @hit' for l in JS},
    },
    "sql-string-build": {
        "why": "SQL built from strings: use parameters to avoid injection.",
        "python": '[(binary_operator left: (string) @s (#match? @s "(?i)(select|insert|update|delete)\\\\s")) '
                  '(call function: (attribute attribute: (identifier) @m) arguments: (argument_list (binary_operator left: (string) @s2 (#match? @s2 "(?i)(select|insert|update|delete)\\\\s")))) '
                  '(string (interpolation)) @fs] @hit',
        **{l: '[(binary_expression left: (string) @s (#match? @s "(?i)(select|insert|update|delete)\\\\s")) '
              '(template_string (template_substitution)) @t] @hit' for l in JS},
        "java": '(binary_expression left: (string_literal) @s (#match? @s "(?i)(select|insert|update|delete)\\\\s")) @hit',
        "csharp": '[(binary_expression left: (string_literal) @s (#match? @s "(?i)(select|insert|update|delete)\\\\s")) (interpolated_string_expression) @i] @hit',
    },
    "await-in-loop": {
        "why": "Sequential awaits in a loop; consider Promise.all / gather.",
        **{l: '[(for_in_statement body: (_ (expression_statement (await_expression)))) '
              '(for_statement body: (_ (expression_statement (await_expression)))) '
              '(while_statement body: (_ (expression_statement (await_expression))))] @hit' for l in JS},
        "python": '(for_statement body: (block (expression_statement (await)))) @hit',
    },
    "mutable-default": {
        "why": "Mutable default arguments are shared between calls.",
        "python": '(default_parameter value: [(list) (dictionary) (set)]) @hit',
    },
    "hardcoded-secret": {
        "why": "Credentials in source; load from configuration or a secret store.",
        "python": '(assignment left: (identifier) @n right: (string) @v (#match? @n "(?i)(password|passwd|secret|api_?key|token)") (#match? @v "^.{8,}$")) @hit',
        **{l: '(variable_declarator name: (identifier) @n value: (string) @v (#match? @n "(?i)(password|passwd|secret|api_?key|token)") (#match? @v "^.{8,}$")) @hit' for l in JS},
    },
    "nested-loops": {
        "why": "Loop inside loop: check for accidental O(n^2) on large data.",
        "python": "(for_statement body: (block (for_statement))) @hit",
        **{l: "[(for_statement body: (statement_block [(for_statement) (for_in_statement)])) "
              "(for_in_statement body: (statement_block [(for_statement) (for_in_statement)]))] @hit" for l in JS},
        "java": "(for_statement body: (block (for_statement))) @hit",
        "go": "(for_statement body: (block (statement_list (for_statement)))) @hit",
    },
    "todo-comment": {
        "why": "Unfinished work recorded in comments.",
        **{l: '((comment) @hit (#match? @hit "(TODO|FIXME|HACK|XXX)")) ' for l in
           ("python", "javascript", "typescript", "tsx", "csharp", "go", "ruby", "php")},
        "java": '([(line_comment) (block_comment)] @hit (#match? @hit "(TODO|FIXME|HACK|XXX)"))',
        "rust": '([(line_comment) (block_comment)] @hit (#match? @hit "(TODO|FIXME|HACK|XXX)"))',
        "kotlin": '([(line_comment) (multiline_comment)] @hit (#match? @hit "(TODO|FIXME|HACK|XXX)"))',
    },
}


def presets() -> dict[str, dict]:
    return {k: {"why": v["why"], "languages": sorted(x for x in v if x != "why")} for k, v in PRESETS.items()}


def search(ix: CodeIndex, query: str | None = None, preset: str | None = None, language: str | None = None,
           path_glob: str | None = None, limit: int = 50) -> dict:
    if ts is None:
        raise ValueError("tree-sitter is not available in this environment")
    if not query and not preset:
        raise ValueError("Pass a tree-sitter query or a preset: " + ", ".join(PRESETS))
    if preset and preset not in PRESETS:
        raise ValueError(f"Unknown preset '{preset}'. Presets: {', '.join(PRESETS)}")
    if query and not language:
        raise ValueError("A raw query needs language= (e.g. python, typescript, java)")
    rows = ix.db.execute("SELECT path, lang FROM files WHERE parser='tree-sitter' ORDER BY path").fetchall()
    local = local_grammars()
    compiled: dict[str, object] = {}
    hits, scanned, errors = [], 0, {}
    for r in rows:
        lang, path = r["lang"], r["path"]
        if language and lang != language:
            continue
        if path_glob and not fnmatch.fnmatch(path, path_glob):
            continue
        if GENERATED_RX.search(path) or lang not in local:
            continue
        pattern = query if query else PRESETS[preset].get(lang)
        if not pattern:
            continue
        if lang not in compiled:
            try:
                compiled[lang] = ts.Query(tslp.get_language(lang), pattern)
            except Exception as e:  # noqa: BLE001 - invalid query for this grammar
                errors[lang] = str(e)[:200]
                compiled[lang] = None
        q = compiled[lang]
        if q is None:
            continue
        try:
            src = (ix.root / path).read_bytes()
        except OSError:
            continue
        scanned += 1
        tree = tslp.get_parser(lang).parse(src)
        caps = ts.QueryCursor(q).captures(tree.root_node)
        nodes = caps.get("hit") or next(iter(caps.values()), [])
        for n in nodes:
            line = n.start_point[0] + 1
            enc = ix.enclosing_symbol(path, line)
            snippet = src[n.start_byte:n.end_byte].decode("utf-8", errors="replace").strip().splitlines()
            hits.append({"at": f"{path}:{line}", "in": enc["qualname"] if enc else "(module)",
                         "code": (snippet[0] if snippet else "")[:160]})
            if len(hits) >= limit:
                break
        if len(hits) >= limit:
            break
    return {"preset": preset, "why": PRESETS[preset]["why"] if preset else None, "files_scanned": scanned,
            "matches": hits, "query_errors": errors}

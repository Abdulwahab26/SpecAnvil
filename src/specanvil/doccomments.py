"""Doc comments for every language, read from the source itself.

Grammars differ in what they expose as documentation (Go and Java expose nothing here), but the convention is
the same everywhere: a comment block directly above the symbol, or a docstring on its first line in Python.
This fills `Symbol.doc` with the first sentence when the parser left it empty, which is what makes natural-language
search ("attach a middleware to every request") land on `Engine.Use`.
"""

from __future__ import annotations

import re

LINE_COMMENT_RX = re.compile(
    r"^\s*(///?|//!|#(?!\[|!|include|define|if|ifdef|ifndef|else|endif|pragma|region|endregion)|--|;;?|%)\s?(.*)$")
ANNOTATION_RX = re.compile(r"^\s*(@[\w.]+(\(.*\))?|#\[.*\]|\[[\w.]+(\(.*\))?\]|template\s*<.*>)\s*$")
NOISE_RX = re.compile(r"^(eslint|prettier|noqa|type:|nolint|go:|pylint|istanbul|ts-|@ts-|region|endregion|todo\b|fixme\b"
                      r"|-{3,}|={3,}|\*{3,}|copyright|licen[sc]e|spdx|shellcheck|c8 |v8 )", re.I)
BLOCK_STRIP_RX = re.compile(r"^\s*/?\*+/?|\*+/\s*$")
DOCSTRING_RX = re.compile(r"^[rRbBuU]{0,2}(\"\"\"|''')(.*)$")
PARAM_TAG_RX = re.compile(r"^[@\\](param|return|returns|throws|see|since|author|deprecated|private|public|api|type)\b", re.I)


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip(" *-=#/")
    if not text or NOISE_RX.match(text) or PARAM_TAG_RX.match(text) or text.startswith("@"):
        return ""
    return text[:200]


def doc_above(lines: list[str], start: int) -> str:
    """First sentence of the comment block directly above line `start` (1-based), skipping annotations."""
    i = start - 2
    hops = 0
    while i >= 0 and hops < 8 and (ANNOTATION_RX.match(lines[i]) or
                                   (lines[i].lstrip().startswith("@") and len(lines[i]) < 200)):
        i -= 1
        hops += 1
    if i < 0:
        return ""
    if not lines[i].strip() and i >= 1 and lines[i - 1].rstrip().endswith("*/"):
        i -= 1  # JSDoc style: one blank line between the block comment and the declaration
    if lines[i].rstrip().endswith("*/"):  # block comment: /** ... */ or /* ... */
        j = i
        while j >= 0 and "/*" not in lines[j]:
            j -= 1
            if i - j > 80:
                return ""
        if j < 0:
            return ""
        for raw in lines[j:i + 1]:
            t = _clean(BLOCK_STRIP_RX.sub("", raw))
            if t:
                return t
        return ""
    if LINE_COMMENT_RX.match(lines[i]):
        j = i
        while j - 1 >= 0 and LINE_COMMENT_RX.match(lines[j - 1]):
            j -= 1
        for raw in lines[j:i + 1]:
            t = _clean(LINE_COMMENT_RX.match(raw).group(2))
            if t:
                return t
    return ""


def docstring_below(lines: list[str], start: int, end: int) -> str:
    """Python docstring: the first statement after the def/class header, however many lines the header spans
    (FastAPI-style signatures with annotated parameters run to hundreds of lines)."""
    depth, k = 0, start - 1
    limit = min(end, len(lines))
    while k < limit:
        ln = lines[k]
        code = ln.split("#", 1)[0] if '"' not in ln and "'" not in ln else ln
        depth += sum(code.count(c) for c in "([{") - sum(code.count(c) for c in ")]}")
        if depth <= 0 and code.rstrip().endswith(":"):
            break
        k += 1
    for k in range(k + 1, min(limit, k + 6)):
        t = lines[k].strip()
        if not t:
            continue
        m = DOCSTRING_RX.match(t)
        if not m:
            return ""
        first = m.group(2).split(m.group(1))[0].strip()
        if not first and k + 1 < len(lines):
            first = lines[k + 1].strip().split(m.group(1))[0].strip()
        return first[:200]
    return ""


def attach_docs(source: str, language: str, symbols: list) -> int:
    """Fill missing `doc` on symbols (objects with name, kind, start_line, end_line, doc). Returns how many."""
    if not symbols:
        return 0
    lines = source.split("\n")
    n = 0
    for sym in symbols:
        if sym.doc or sym.kind in ("key", "section"):
            continue
        doc = ""
        if language == "python" and sym.kind in ("function", "method", "class"):
            doc = docstring_below(lines, sym.start_line, sym.end_line)
        doc = doc or doc_above(lines, sym.start_line)
        if doc:
            sym.doc = doc
            n += 1
    return n

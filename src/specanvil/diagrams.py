"""Mermaid diagrams for every graph-producing analysis: call graph, class hierarchy, file dependencies,
module coupling, signal chains (as sequence diagrams) and the tectonic map."""

from __future__ import annotations

import re
from collections import deque

from .graph import CodeGraph, class_hierarchy, coupling
from .index import CodeIndex


def _id(s: str) -> str:
    return "n" + re.sub(r"\W", "_", s)[:60]


def _label(s: str) -> str:
    return s.replace('"', "'")[:60]


def render(ix: CodeIndex, kind: str, target: str = "", depth: int = 2, limit: int = 40) -> str:
    if kind == "call":
        g = CodeGraph(ix)
        rows = ix.find_symbol(target)
        if not rows:
            raise ValueError(f"Symbol not found: {target}")
        root = rows[0]["id"]
        lines = ["flowchart LR"]
        seen, q, n = {root}, deque([(root, 0)]), 0
        lines.append(f'  {_id(str(root))}["{_label(g.sym[root]["qualname"])}"]:::focus')
        while q and n < limit:
            cur, d = q.popleft()
            if d >= depth:
                continue
            for nxt, _ in g.callees(cur):
                lines.append(f'  {_id(str(cur))} --> {_id(str(nxt))}["{_label(g.sym[nxt]["qualname"])}"]')
                n += 1
                if nxt not in seen:
                    seen.add(nxt)
                    q.append((nxt, d + 1))
            for prv, _ in g.callers(cur) if d == 0 else []:
                lines.append(f'  {_id(str(prv))}["{_label(g.sym[prv]["qualname"])}"] --> {_id(str(cur))}')
                n += 1
        lines.append("  classDef focus fill:#7c9cff,color:#fff")
        return "\n".join(lines)
    if kind == "class":
        h = class_hierarchy(ix, target)
        lines = ["classDiagram"]
        for a in h["ancestors"]:
            lines.append(f"  {a['extends']} <|-- {a['class']}")
        for d in h["descendants"][:limit]:
            lines.append(f"  {d['extends']} <|-- {d['class']}")
        return "\n".join(lines)
    if kind == "deps":
        lines = ["flowchart LR", f'  {_id(target)}["{_label(target)}"]:::focus']
        for (dst,) in ix.db.execute("SELECT dst FROM file_deps WHERE src=? LIMIT ?", (target, limit)):
            lines.append(f'  {_id(target)} --> {_id(dst)}["{_label(dst)}"]')
        for (src,) in ix.db.execute("SELECT src FROM file_deps WHERE dst=? LIMIT ?", (target, limit)):
            lines.append(f'  {_id(src)}["{_label(src)}"] --> {_id(target)}')
        lines.append("  classDef focus fill:#7c9cff,color:#fff")
        return "\n".join(lines)
    if kind == "modules":
        lines = ["flowchart TB"]
        for m in coupling(ix, limit):
            for d in m["depends_on"]:
                lines.append(f'  {_id(m["module"])}["{_label(m["module"])}"] --> {_id(d)}["{_label(d)}"]')
        return "\n".join(lines)
    if kind == "sequence":
        from .insights import signal_chains
        chains = signal_chains(ix, entry=target or None, limit=1)
        if not chains:
            raise ValueError("No call chain found from that entry point")
        steps = [c.split(" (")[0] for c in chains[0]["chain"]]
        lines = ["sequenceDiagram"]
        for a, b in zip(steps, steps[1:]):
            lines.append(f"  {a.split('.')[0][:30]}->>{b.split('.')[0][:30]}: {b.split('.')[-1][:40]}()")
        return "\n".join(lines)
    if kind == "tectonic":
        from .insights import tectonic_map
        tm = tectonic_map(ix)
        lines = ["flowchart TB"]
        for i, p in enumerate(tm["plates"][:12]):
            lines.append(f'  subgraph P{i}["{_label(p["plate"])} · {p["files"]} files · cohesion {p["cohesion"]}"]')
            for f in p["sample"][:4]:
                lines.append(f'    {_id(f)}["{_label(f.split("/")[-1])}"]')
            lines.append("  end")
        return "\n".join(lines)
    raise ValueError("kind must be call, class, deps, modules, sequence or tectonic")


def to_html(mermaid: str, title: str = "SpecAnvil diagram") -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"></script></head>
<body style="font-family:system-ui;margin:24px"><h3>{title}</h3><pre class="mermaid">{mermaid}</pre>
<script>mermaid.initialize({{startOnLoad:true}});</script></body></html>"""

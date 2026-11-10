"""Self-contained HTML report: repo map, spec coverage, connectors and token savings."""

from __future__ import annotations

import html
import json
import time
from pathlib import Path

from .config import load_config, state_dir
from .index import CodeIndex
from .specs import drift, list_specs

CSS = """
:root{--bg:#0d1117;--panel:#161b22;--line:#30363d;--text:#e6edf3;--muted:#8b949e;--accent:#7c9cff;--ok:#3fb950;--warn:#d29922;--bad:#f85149}
@media (prefers-color-scheme: light){:root{--bg:#f6f8fa;--panel:#fff;--line:#d0d7de;--text:#1f2328;--muted:#59636e;--accent:#3b5bdb;--ok:#1a7f37;--warn:#9a6700;--bad:#cf222e}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:32px 16px}h1{font-size:24px;margin:0}h2{font-size:15px;margin:28px 0 10px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.06em}
.sub{color:var(--muted);margin-top:4px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-top:20px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}.tile b{display:block;font-size:26px;font-variant-numeric:tabular-nums}.tile span{color:var(--muted);font-size:12px}
.hero b{color:var(--ok)}.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;overflow-x:auto}
table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}th{color:var(--muted);font-weight:500;font-size:12px}
.bar{height:8px;border-radius:4px;background:var(--line);overflow:hidden;min-width:80px}.bar i{display:block;height:100%;background:var(--accent)}
.chips span{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:2px 10px;margin:3px 4px 3px 0;font-size:12px}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media(max-width:760px){.two{grid-template-columns:1fr}}
.flow{display:flex;flex-wrap:wrap;gap:6px;align-items:center}.flow span{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:4px 8px;font-family:ui-monospace,monospace;font-size:12px}.flow em{color:var(--muted);font-style:normal}
"""


def _e(x) -> str:
    return html.escape(str(x))


def write_report(root: Path, ix: CodeIndex | None = None) -> Path:
    root = root.resolve()
    ix = ix or CodeIndex(root)
    cfg = load_config(root)
    st = ix.stats()
    mods = ix.modules()
    sv = ix.savings()
    dr = drift(root, ix)
    specs = list_specs(root)
    prof = (cfg.get("profile") or {}).get("stack", {})
    uncovered = set(dr["modules_without_spec_refs"])

    max_lines = max([l["lines"] or 0 for l in st["languages"]] + [1])
    lang_rows = "".join(
        f"<tr><td>{_e(l['lang'])}</td><td>{l['files']}</td><td>{l['lines']:,}</td>"
        f"<td><div class=bar><i style='width:{100 * (l['lines'] or 0) / max_lines:.0f}%'></i></div></td></tr>"
        for l in st["languages"][:12])
    mod_rows = "".join(
        f"<tr><td><code>{_e(m['m'])}</code></td><td>{m['files']}</td><td>{m['lines']:,}</td><td>{m['syms']}</td>"
        f"<td class={'bad' if m['m'] in uncovered else 'ok'}>{'no spec refs' if m['m'] in uncovered else 'covered'}</td></tr>"
        for m in mods[:40])
    tool_rows = "".join(
        f"<tr><td><code>{_e(t['tool'])}</code></td><td>{t['calls']}</td><td>{t['returned_tokens']:,}</td>"
        f"<td>{t['baseline_tokens']:,}</td><td class=ok>{t['saved_pct']}%</td></tr>" for t in sv["by_tool"]) or \
        "<tr><td colspan=5 class=sub>No MCP calls yet. Savings appear here as Claude uses the specforge tools.</td></tr>"

    def chips(items):
        return "".join(f"<span>{_e(i)}</span>" for i in items) or "<span class=sub>none detected</span>"

    stack_html = "".join(
        f"<tr><td>{label}</td><td class=chips>{chips(prof.get(key, []))}</td></tr>"
        for label, key in [("Frontend", "frontend"), ("Backend", "backend"), ("Data access", "orm"),
                           ("Databases", "databases"), ("Tests", "test_frameworks"), ("CI", "ci"),
                           ("Tickets", "ticket_systems")])
    issues = dr["issues"]
    changed = sum(i["issue"] == "changed" for i in issues)
    missing = sum(i["issue"] == "missing" for i in issues)
    savings_tile = f"{sv['saved_pct']}%" if sv["calls"] else "—"
    flow = "".join(f"<span>/sf:{c}</span><em>→</em>" for c in
                   ["groom", "design", "plan", "be + fe", "db", "qa", "e2e", "verify", "ship"])[:-10]

    page = f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>SpecForge report</title><style>{CSS}</style></head><body><main>
<h1>SpecForge · {_e(root.name)}</h1>
<div class=sub>Generated {time.strftime('%Y-%m-%d %H:%M')} · local analysis only, nothing left this machine</div>
<div class=grid>
<div class=tile><b>{st['files']:,}</b><span>files indexed</span></div>
<div class=tile><b>{st['symbols']:,}</b><span>symbols</span></div>
<div class=tile><b>{len(mods)}</b><span>modules</span></div>
<div class=tile><b>{len(specs)}</b><span>spec files</span></div>
<div class=tile><b>~{st['repo_tokens']:,}</b><span>tokens to read the repo in full</span></div>
<div class="tile hero"><b>{savings_tile}</b><span>tokens saved by the code index ({sv['saved_tokens']:,} over {sv['calls']} calls)</span></div>
</div>
<h2>Workflow</h2><div class="panel flow">{flow}</div>
<div class=two>
<div><h2>Languages</h2><div class=panel><table><tr><th>Language</th><th>Files</th><th>Lines</th><th></th></tr>{lang_rows}</table></div></div>
<div><h2>Detected stack</h2><div class=panel><table>{stack_html}</table></div></div>
</div>
<h2>Token savings by tool</h2><div class=panel><table><tr><th>Tool</th><th>Calls</th><th>Returned</th><th>Whole-file baseline</th><th>Saved</th></tr>{tool_rows}</table></div>
<h2>Modules &amp; spec coverage</h2><div class=panel><table><tr><th>Module</th><th>Files</th><th>Lines</th><th>Symbols</th><th>Spec</th></tr>{mod_rows}</table></div>
<div class=two>
<div><h2>Spec drift</h2><div class=panel>
<p><b class=ok>{dr['verified_refs']}</b> verified refs · <b class=warn>{changed}</b> changed · <b class=bad>{missing}</b> missing</p>
<p class=sub>{_e(dr['summary'])}</p></div></div>
<div><h2>MCP connectors</h2><div class="panel chips">{chips(cfg.get('connectors') or [])}</div></div>
</div>
</main></body></html>"""
    out = state_dir(root) / "report.html"
    out.write_text(page, encoding="utf-8")
    return out


def report_json(root: Path) -> str:
    ix = CodeIndex(root)
    return json.dumps({"stats": ix.stats(), "savings": ix.savings()}, indent=2)

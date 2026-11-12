// Pure helpers (no VS Code API) so they can be tested with `node --test`.
'use strict';

const SEVERITY = { missing: 'error', changed: 'warning', unverified: 'information' };

/** Parse the output of `specanvil drift --json`. Returns { summary, issues } or throws on invalid output. */
function parseDrift(stdout) {
  const start = stdout.indexOf('{');
  if (start < 0) throw new Error('specanvil drift printed no JSON');
  const data = JSON.parse(stdout.slice(start));
  const issues = Array.isArray(data.issues) ? data.issues : [];
  return {
    summary: String(data.summary || ''),
    issues: issues
      .filter((i) => i && i.spec && i.ref && SEVERITY[i.issue])
      .map((i) => ({ spec: i.spec, ref: i.ref, issue: i.issue, detail: String(i.detail || ''), severity: SEVERITY[i.issue] })),
  };
}

/** Zero-based positions of a code reference (`path::Qualname`) in a spec's text: [{ line, start, end }]. */
function findReference(text, ref) {
  const out = [];
  const lines = text.split(/\r?\n/);
  for (let line = 0; line < lines.length; line++) {
    let from = 0;
    for (;;) {
      const at = lines[line].indexOf(ref, from);
      if (at < 0) break;
      out.push({ line, start: at, end: at + ref.length });
      from = at + ref.length;
    }
  }
  return out;
}

/** Group issues by spec file: Map<spec path, issue[]>. */
function bySpec(issues) {
  const map = new Map();
  for (const i of issues) {
    if (!map.has(i.spec)) map.set(i.spec, []);
    map.get(i.spec).push(i);
  }
  return map;
}

function message(issue) {
  const what = { missing: 'no longer exists', changed: 'changed since this spec was verified', unverified: 'has not been verified' }[issue.issue];
  return `${issue.ref} ${what}${issue.detail ? ` (${issue.detail})` : ''}`;
}

module.exports = { parseDrift, findReference, bySpec, message, SEVERITY };

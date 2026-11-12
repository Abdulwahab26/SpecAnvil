'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { parseDrift, findReference, bySpec, message } = require('../lib/drift');

test('parseDrift keeps known issue kinds and maps severities', () => {
  const out = 'note before json\n' + JSON.stringify({
    summary: '3 verified, 1 changed, 1 missing, 0 unverified',
    issues: [
      { spec: 'specs/a.md', ref: 'src/a.py::f', issue: 'missing', detail: 'symbol not found' },
      { spec: 'specs/a.md', ref: 'src/a.py::g', issue: 'changed', detail: 'body changed' },
      { spec: 'specs/b.md', ref: 'src/b.py::h', issue: 'unverified' },
      { spec: 'specs/b.md', ref: 'src/b.py::i', issue: 'something-else' },
      null,
    ],
  });
  const res = parseDrift(out);
  assert.strictEqual(res.summary, '3 verified, 1 changed, 1 missing, 0 unverified');
  assert.deepStrictEqual(res.issues.map((i) => i.severity), ['error', 'warning', 'information']);
  assert.strictEqual(bySpec(res.issues).get('specs/a.md').length, 2);
  assert.match(message(res.issues[0]), /src\/a\.py::f no longer exists \(symbol not found\)/);
});

test('parseDrift rejects output without JSON', () => {
  assert.throws(() => parseDrift('command not found'));
});

test('findReference returns every occurrence with zero-based positions', () => {
  const text = '# Orders\r\n\r\nTotals: `src/a.py::total` and again `src/a.py::total`.\nNothing here.\n';
  assert.deepStrictEqual(findReference(text, 'src/a.py::total'), [
    { line: 2, start: 9, end: 24 },
    { line: 2, start: 37, end: 52 },
  ]);
  assert.deepStrictEqual(findReference(text, 'src/zzz.py::none'), []);
});

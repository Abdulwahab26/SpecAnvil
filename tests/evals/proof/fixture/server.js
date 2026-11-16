// Zero-dependency shop: one page and a JSON API. Used by the proof demo and its tests.
const http = require('http');
const crypto = require('crypto');
const { quote } = require('./pricing');

const PORT = Number(process.env.PORT || 4173);

const PAGE = `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Shop</title></head><body>
<h1>Shop</h1>
<form id="f">
  <label>Customer type <select name="customer"><option>standard</option><option>silver</option><option>gold</option></select></label>
  <label>Item <select name="item"><option>widget</option><option>gadget</option></select></label>
  <label>Quantity <input name="qty" type="number" value="1"></label>
  <button>Get quote</button>
</form>
<p role="status" id="out"></p>
<script>
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  const q = new URLSearchParams(new FormData(e.target));
  const r = await fetch('/api/quote?' + q);
  const j = await r.json();
  document.getElementById('out').textContent = r.ok
    ? 'Total: ' + (j.total / 100).toFixed(2) + (j.discount ? ' (discount ' + (j.discount / 100).toFixed(2) + ')' : '')
    : 'Error: ' + j.error;
});
</script></body></html>`;

http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/api/quote') {
    res.setHeader('content-type', 'application/json');
    try {
      const body = quote(url.searchParams.get('customer'), url.searchParams.get('item'), Number(url.searchParams.get('qty')));
      // quoteId and createdAt differ on every call; proof masks them instead of reporting them
      res.end(JSON.stringify({ ...body, quoteId: crypto.randomUUID(), createdAt: new Date().toISOString() }));
    } catch (e) {
      res.statusCode = 400;
      res.end(JSON.stringify({ error: e.message }));
    }
    return;
  }
  res.setHeader('content-type', 'text/html');
  res.end(PAGE);
}).listen(PORT, () => console.log(`shop listening on ${PORT}`));

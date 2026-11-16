const { test, expect } = require('@playwright/test');

// Adversarial probes for SHOP-77, written from the ticket: boundaries and inputs the happy-path tests skip.
test.describe('@probe SHOP-77', () => {
  test('gold order of exactly 100.00 gets 15%', async ({ request }) => {
    const res = await request.get('/api/quote?customer=gold&item=widget&qty=4');
    expect((await res.json()).discount).toBe(1500);
  });

  test('gold order just under 100.00 keeps 10%', async ({ request }) => {
    const res = await request.get('/api/quote?customer=gold&item=widget&qty=3');
    expect((await res.json()).discount).toBe(750);
  });

  test('a fractional quantity is rejected, not rounded', async ({ request }) => {
    const res = await request.get('/api/quote?customer=gold&item=widget&qty=4.5');
    expect(res.status()).toBe(400);
  });
});

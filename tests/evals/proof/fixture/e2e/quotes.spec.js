const { test, expect } = require('@playwright/test');

// One quote per customer type, through the page. Each test attaches the page content under a `proof:` name so
// `specanvil proof` can compare what the user saw before and after a change.
for (const customer of ['standard', 'silver', 'gold']) {
  test(`${customer} customer gets a quote for 6 widgets`, async ({ page }, testInfo) => {
    await page.goto('/');
    await page.getByLabel('Customer type').selectOption(customer);
    await page.getByLabel('Quantity').fill('6');
    await page.getByRole('button', { name: 'Get quote' }).click();
    await expect(page.getByRole('status')).toContainText('Total:');
    await testInfo.attach('proof:quote', { body: await page.locator('body').ariaSnapshot(), contentType: 'text/plain' });
  });
}

test('small gold order of 2 widgets', async ({ request }) => {
  const res = await request.get('/api/quote?customer=gold&item=widget&qty=2');
  expect(res.ok()).toBeTruthy();
});

test('unknown item is rejected', async ({ request }) => {
  const res = await request.get('/api/quote?customer=standard&item=rocket&qty=1');
  expect(res.status()).toBe(400);
});

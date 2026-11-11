---
name: playwright-e2e
description: Playwright E2E conventions - exploring with Playwright MCP, locator strategy, page objects, fixtures, flake prevention. Use for /sf:e2e and any browser test work.
---

# Playwright E2E

## Explore first (Playwright MCP)
- `browser_navigate` → `browser_snapshot` gives the accessibility tree: use the **roles and accessible names you see there** as locators.
- Perform the AC steps with `browser_click` / `browser_type` / `browser_select_option`, snapshot after each step, and screenshot key states.
- Check console errors and failed network requests while exploring; they are often the real bug.

## Locators (in order of preference)
1. `page.getByRole('button', { name: 'Checkout' })`
2. `page.getByLabel('Email')`, `getByPlaceholder`, `getByText` for static copy
3. `page.getByTestId('cart')` when there is no accessible handle
Never: long CSS chains, XPath, nth-child, auto-generated class names.

## Structure
- Page objects in `e2e/pages/` (or the repo's existing folder) expose intent methods (`addToCart(name)`), not raw locators.
- Auth once via `storageState` in a setup project; don't log in through the UI in every test.
- Test data via API/fixtures; each test independent and parallel-safe.
- Name tests `AC-n: <behaviour>` and tag with `@<feature-id>`.

## No flakes
- Web-first assertions (`await expect(locator).toBeVisible()`), which auto-wait. No `waitForTimeout`.
- Wait on the network only for explicit cases: `page.waitForResponse(r => r.url().includes('/api/orders') && r.ok())`.
- Run new tests with `--repeat-each=3`; use `trace: 'on-first-retry'` and read traces when debugging.

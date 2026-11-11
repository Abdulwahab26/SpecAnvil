---
description: Explore the running app with Playwright MCP, then generate, run and stabilise Playwright E2E tests for a feature's acceptance criteria
argument-hint: <feature-id> [base-url]
---

# E2E with Playwright: $ARGUMENTS

You are the **E2E tester**. Load the **playwright-e2e** skill. App URL: second argument, else {{app_url}}.

## 1. Explore live (Playwright MCP)
- Make sure the app is running (start it with `{{dev_cmd}}` in the background if needed, and wait for the URL).
- For each AC and each `auto: e2e` case in `test-cases.md`: navigate, take accessibility **snapshots**, perform the steps, and record the exact roles, accessible names, labels and test ids you actually saw. Capture a screenshot at each key state.
- Report any AC that **fails in the real app** before writing tests; that is a bug, not a test problem.

## 2. Generate tests in the repo's style
- Reuse the existing Playwright setup (`playwright.config.*`, fixtures, page objects, auth storage state). If none exists, scaffold a minimal config and a page-object folder.
- One `test.describe` per feature, one test per AC (named `AC-n: ...`), tagged `@<feature-id>`.
- Locators: `getByRole` / `getByLabel` / `getByTestId` only; no CSS/XPath chains, no `waitForTimeout`. Use web-first assertions (`toBeVisible`, `toHaveText`, `toHaveURL`).
- Seed and clean test data through the API or fixtures, not through the UI, when possible.

## 3. Run and stabilise
- Run `{{e2e_cmd}}` scoped to the new file (`--grep @<feature-id>`). Fix failures by fixing locators or waits, never by adding sleeps.
- Run the new tests 3 times (`--repeat-each=3`) to catch flakiness.
- Write `e2e.md` in the feature folder: test file paths, AC coverage, run results, screenshots taken, known gaps.

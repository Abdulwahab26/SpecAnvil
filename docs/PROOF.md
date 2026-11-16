# Proof of a change

AI coding tools write code faster than people can review it, and the code they write tends to look right: it is tidy and its tests pass. `specanvil proof` attaches evidence to a change before it merges, so a reviewer reads the surprises instead of the whole diff, and a change with no surprises does not wait for a reviewer at all.

It runs the repository's Playwright tests on the code **before** and **after** the change, compares what the application did, sorts each difference against what the ticket asked for, scores the risk with fixed rules, and writes a tamper-evident record.

No container and no second checkout are used. The "before" run is the last commit: the change is put in a git stash for the duration of that run, so the installed dependencies, `.env` files and everything else git ignores stay where they are.

## The five stages

| Stage | Who does it | What it produces |
|---|---|---|
| 1. Intent contract | The `intent-verifier` agent, from the ticket text only. Its only tool is `Write`, so it cannot read the code or the diff. Run it on a different model from the one that wrote the change. | `intent.json`: rules for what must change and what must not |
| 2. Behaviour proof | `specanvil proof run` (code, no model) | Differences in test results, API responses and page snapshots between the two runs |
| 3. Adversarial probes | The `adversary` agent writes `@probe` Playwright tests from the ticket | Failing probes, each with the command that reproduces it |
| 4. Risk routing | Fixed rules in `proofrecord.py` (code, no model) | **Cleared automatically** or **routed to a person**, with a short brief |
| 5. Evidence record | `specanvil proof run` (code, no model) | One JSON record per change in `.specanvil/proof/records/`, chained by hash |

In an AI coding tool, `/sa:proof <ticket>` runs all five. `/sa:flow` runs it between E2E and verify, and stops at a human gate when the change is routed to a person, also with `--auto`.

## Running it

```bash
git add src/pricing.js                       # stage what should be proven
specanvil proof run SHOP-77 --author-model opus --author-tool "Claude Code"

specanvil proof run SHOP-77 --scope all      # everything uncommitted instead of the staged change
specanvil proof run SHOP-77 --calibrate      # run the old code twice; ignore values that differ on their own
specanvil proof show                         # the latest record as Markdown (--json for the full record)
specanvil proof list
specanvil proof approve --by "Dana Reviewer" --reason "silver change agreed with the client"
specanvil proof verify                       # check the evidence chain
specanvil proof restore                      # after an interrupted run: put the stashed change back
```

`run` exits with 0 when the change is cleared automatically, 3 when it is routed to a person and 1 when the run could not be made.

What one run does:

1. Checks that it can compare: a git repository with a commit, no merge or rebase in progress, something staged, no unfinished proof run, nothing already listening on the app URL.
2. Reads the intent contract and the list of changed files, and writes a lock file in the git directory.
3. `git stash push --include-untracked`. The working tree is now the last commit.
4. Copies the **changed** version of the E2E tests (`proof.test_paths`) on top of the old code, so both runs execute the same tests. A test written for the change then fails on the old code and passes on the new: evidence that the behaviour changed.
5. Runs the E2E command with `--trace on --reporter=json`. The trace holds every API response each test saw.
6. Restores the change (the staged tree only for `--scope staged`, the whole stash for `--scope all`) and runs the same tests again.
7. Puts the working tree back exactly as it was: index, unstaged edits, untracked files.
8. Compares, sorts, scores and writes the record.

## What is compared

Per test, matched by file and title:

- **Result**: passed, failed, flaky, skipped.
- **API responses**: status and JSON body of every `fetch`/XHR call made by the page and every call made with Playwright's `request` fixture, compared field by field. Pages, scripts, styles, images and fonts are left out.
- **Request bodies** sent by the page.
- **Page snapshots**: any text a test attaches under a name starting with `proof:`. The usual one is the accessibility snapshot:

```js
await testInfo.attach('proof:cart', { body: await page.locator('main').ariaSnapshot(), contentType: 'text/plain' });
```

Values that change on their own are masked before comparing: UUIDs, ISO timestamps, JWTs, and the keys in `proof.mask_keys` (`createdAt`, `requestId`, `etag` and similar). Passwords, tokens and API keys are masked too and never reach a record. `--calibrate` finds the rest by running the old code twice and dropping whatever differs between those two runs. The record states how many values were masked or dropped.

## The intent contract

```json
{
  "ticket": "SHOP-77",
  "model": "sonnet",
  "kind": "feature",
  "summary": "Gold customers get 15% off instead of 10% when the order subtotal is 100.00 or more.",
  "rules": [
    {"id": "R1", "expect": "change", "text": "Gold orders of 100.00 or more use a 15% discount.",
     "match": {"endpoint": "GET /api/quote*customer=gold*"}},
    {"id": "R2", "expect": "no_change", "text": "Quotes for silver customers do not change.",
     "match": {"endpoint": "GET /api/quote*customer=silver*"}}
  ],
  "unclear": []
}
```

It lives in the ticket's feature folder (`specs/features/<ID>-<slug>/intent.json`) or, without one, in `.specanvil/proof/intents/<ID>.json`. `specanvil proof intent <ID>` checks its shape.

`match` keys are combined with AND, use `*` wildcards and ignore case: `endpoint` (`METHOD /path?query`, query parameters sorted by name), `test` (test title), `path` (JSON path such as `$.total`) and `kind` (`response_body`, `response_status`, `request_body`, `snapshot`, `call_added`, `call_removed`, `call_count`).

Each difference gets one class:

| Class | Meaning |
|---|---|
| intended | matches a `change` rule: the ticket asked for it |
| violation | matches a `no_change` rule: the ticket says this must stay the same |
| unintended | matches no rule: nobody asked for it |
| regression | a test that passed on the old code fails on the change |
| fixed | a test that failed on the old code passes on the change |

A `change` rule that no difference matches is reported as **no evidence**: either the change was not made or no test shows it.

## Risk routing

A change is cleared automatically only when no gate applies **and** the score is at most `proof.auto_max_score` (24). Gates always send the change to a person:

- a difference that is unintended, a violation or a regression
- a failed probe, or a new test that fails on the change
- no intent contract, an unclear ticket, or a requested change with no evidence
- the intent contract was written by the same model as the code
- sensitive code touched (authentication, sessions, permissions, payments, migrations, schemas, `.sql`)
- a dependency manifest changed (the old code ran with the installed packages)
- test files deleted
- the change edits the proof settings that decide how it is checked
- an E2E run produced no report, or nothing was compared

Points add up to a 0 to 100 score from the size of the change and the findings. Every gate and every point is stored in the record with its reason.

A person can always override the routing in either direction, and the override is recorded with name and reason: `specanvil proof approve | reject | hold <record> --by ... --reason ...`. No agent runs these commands.

## The evidence record

Each record holds: the ticket, the base commit and the tree that was tested, the model and tool that wrote the change, the intent contract and its hash, both test runs, every difference with its class and values, failed probes, the risk score with reasons, the routing decision with its brief, and a list of **what was not checked**. Decisions by people are records in the same chain.

Each record stores the hash of the record before it and its own hash. `specanvil proof verify` fails when a record was edited, removed or inserted. Commit the records with the change: the pull-request comment (`specanvil pr-comment`) shows the proof of the records the branch adds, and `specanvil report` lists them all.

Records contain response values from your test data. Use test data you are willing to commit.

## Settings

In `.specanvil/config.json`:

```json
{
  "app_url": "http://localhost:3000",
  "proof": {
    "e2e_cmd": "npx playwright test",
    "test_paths": ["e2e"],
    "start_cmd": null,
    "env": {"CI": "1"},
    "mask_keys": ["created_?at", "request_?id"],
    "mask_patterns": ["ord_[a-z0-9]+"],
    "ignore_urls": ["GET /api/health*"],
    "auto_max_score": 24
  }
}
```

| Setting | Default | Meaning |
|---|---|---|
| `e2e_cmd` | detected E2E command, else `npx playwright test` | Run directly, never through a shell, and only if it starts with a recognised test runner |
| `test_paths` | `[]` | E2E folders or files. Set this: without it each version runs its own copy of the tests |
| `start_cmd` | none | How to start the app, only when the Playwright config has no `webServer`. Proof waits for `ready_url` (default `app_url`) |
| `env` | `{}` | Extra environment for the E2E command. `SPECANVIL_PROOF` is set to `baseline` or `candidate` |
| `mask_keys`, `mask_patterns`, `ignore_urls` | see `proof.py` | What to leave out of the comparison |
| `timeout` | 1800 | Seconds for one E2E run |

Let Playwright start the app (`webServer` with `reuseExistingServer: false`): each run then gets a fresh server on the right code.

## Limits

- **Only what the tests exercise is compared.** A code path no E2E test reaches does not appear. The record says so every time.
- **A cleared change is not a guarantee.** It means: no test saw behaviour nobody asked for, no probe broke it, and the rules found nothing risky.
- **The contract is only as good as the ticket.** Vague tickets are routed to a person.
- **Dependency changes** are compared against the installed packages, so they are always routed to a person.
- **Database state** is not reset between the runs. Tests that seed their own data are unaffected; others should run against a disposable database.
- **Line coverage of the changed code** is not measured yet.
- **Your uncommitted work is in a stash during a run.** The stash is restored in a `finally` block and on `SIGTERM`; after a hard kill, `specanvil proof restore` puts it back, and no new run starts until it has. Files created during a run that git does not ignore are kept in a second stash, never deleted. Do not edit files while a run is in progress.

## Try it

`tests/evals/proof/` holds a small shop, a ticket, an intent contract, probes and three planted changes that all pass the shop's own tests:

```bash
python tests/evals/run_proof_demo.py wrong      # gives silver customers a discount the ticket never mentioned
python tests/evals/run_proof_demo.py --all
```

| Change | Routed | Findings |
|---|---|---|
| `safe` | cleared automatically, risk 0 | 3 asked-for differences |
| `wrong` | to a person, risk 75 | 2 violations and 1 unintended difference for silver customers |
| `boundary` | to a person, risk 20 | 1 failed probe: an order of exactly 100.00 |

Measured with Playwright 1.65 and Chrome; one proof takes about 7 seconds on this fixture. Three planted changes on one small application show that the mechanism works; they are not a detection rate.

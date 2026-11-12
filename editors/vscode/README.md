# SpecAnvil for VS Code

Shows what the local SpecAnvil index knows, inside the editor. It runs the `specanvil` command in the workspace
folder; nothing is sent anywhere.

| Command | What it does |
|---|---|
| SpecAnvil: Check Spec Drift | Runs `specanvil drift --json` and lists every broken code reference in the Problems panel, on the line of the spec that holds it. Missing symbols are errors, changed code is a warning, unverified references are information. Re-runs on save (`specanvil.checkDriftOnSave`). |
| SpecAnvil: Mark Specs as Verified | Runs `specanvil drift --lock` after you have reviewed the changes. |
| SpecAnvil: Show Health Report | Opens `.specanvil/report.html` beside the editor. |
| SpecAnvil: Show Diagram | Module, dependency, call, class, sequence or tectonic diagram; call, class and sequence start from the symbol under the cursor. |
| SpecAnvil: Show Change Risk for Current Changes | Risk per changed symbol against `specanvil.riskBase` (default `HEAD`), with spec drift. |
| SpecAnvil: Rebuild Index | Runs `specanvil index --full`. |

The extension activates in folders that contain `.specanvil/config.json` (created by `specanvil init`).

## Requirements
SpecAnvil 0.11 or later on the `PATH`, or its location in the `specanvil.path` setting.

## Install from source
The extension is plain JavaScript with no dependencies and no build step.

```bash
cd editors/vscode
npm test                                   # unit tests for the parsing helpers
npx --yes @vscode/vsce package             # produces specanvil-0.1.0.vsix
code --install-extension specanvil-0.1.0.vsix
```

## Status
Experimental. The parsing helpers are unit-tested in CI; the editor integration has not yet been published to the
Marketplace.

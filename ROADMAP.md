# Roadmap

What is planned, in rough priority order. Nothing here is promised by a date. Open an issue to argue for a change in order.

## Now: get it into people's hands
- [ ] Publish `specanvil` to PyPI (steps in [docs/RELEASING.md](docs/RELEASING.md))
- [x] Rename the GitHub repository to `SpecAnvil`
- [ ] Make the repository public
- [ ] A 60-second demo recording and a quickstart on a sample repo

## Next: wider evidence
- [x] C# in the multi-repo retrieval gate (0.10)
- [x] Rust, PHP, Ruby and Kotlin in the retrieval gate (0.11)
- [ ] Paraphrased tasks: 8/12 with embeddings, 3/12 without. Target 10/12 and 6/12
- [x] Workflow eval harness: a fixed ticket on a fixture repo, scored on six checks (0.11)
- [ ] Publish workflow eval scores per AI coding tool and model

## Next: hardening
- [x] Type errors to zero (0.11)
- [ ] Coverage floor to 85%
- [x] Call graph through injected dependencies with declared types: Spring, NestJS, ASP.NET Core (0.11)
- [ ] Call graph through providers and factories, wiring by name, and receivers returned by a call
- [ ] Secret guard for tools that add a hook system (today: Claude Code, Cursor, GitHub Copilot)
- [ ] Connector servers for Windsurf and Codex once their project-file env syntax is documented

## Later
- [x] Pull-request comment with change risk and spec drift from the generated GitHub Action (0.10)
- [x] VS Code extension, experimental: drift, health report, diagrams, change risk (0.11)
- [ ] Publish the VS Code extension to the Marketplace
- [x] Documentation site configuration with a strict build in CI (0.11)
- [ ] Publish the documentation site (GitHub Pages, once the repository is public)
- [ ] HTTP transport for the MCP server (shared team index)

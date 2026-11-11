# Roadmap

What is planned, in rough priority order. Nothing here is promised by a date. Open an issue to argue for a change in order.

## Now: get it into people's hands
- [ ] Publish `specanvil` to PyPI (steps in [docs/RELEASING.md](docs/RELEASING.md))
- [x] Rename the GitHub repository to `SpecAnvil`
- [ ] Make the repository public
- [ ] A 60-second demo recording and a quickstart on a sample repo

## Next: wider evidence
- [x] C# in the multi-repo retrieval gate (0.10)
- [ ] Rust, PHP, Ruby and Kotlin in the retrieval gate
- [ ] Paraphrased tasks: 8/12 with embeddings, 3/12 without. Target 10/12 and 6/12
- [ ] A workflow eval: does `/sa:flow` on a fixed ticket in a sample repo end with passing tests

## Next: hardening
- [ ] Type errors to zero (ratchet: 100 at 0.9, lower in 0.10; see `ci.yml`)
- [ ] Coverage floor to 85%
- [ ] Call graph through dependency injection (Spring, NestJS, ASP.NET) and dynamic dispatch
- [ ] Secret guard for tools that add a hook system (today: Claude Code, Cursor, GitHub Copilot)
- [ ] Connector servers for Windsurf and Codex once their project-file env syntax is documented

## Later
- [x] Pull-request comment with change risk and spec drift from the generated GitHub Action (0.10)
- [ ] VS Code extension: health report, diagrams and drift in the editor
- [ ] Documentation site
- [ ] HTTP transport for the MCP server (shared team index)

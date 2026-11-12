# SpecAnvil

SpecAnvil sets up spec-driven development in an existing repository with one command. It builds a local code index, detects the stack and conventions, and writes the configuration an AI coding tool needs to work from specifications: a code-index MCP server, project-specific skills, role agents, lifecycle workflows and connectors.

Indexing, detection and analysis run locally and make no network calls.

## Install

```bash
pipx install git+https://github.com/Abdulwahab26/SpecAnvil
specanvil doctor
```

Requires Python 3.10 or later.

## First run

```bash
cd your-repo
specanvil init
```

`init` indexes the repository, detects languages, frameworks, databases, test tools and the ticket system, and writes specs, workflows, agents, skills and the MCP configuration for each AI coding tool the repository uses. Then, in the tool:

| Step | Command (Claude Code syntax) |
|---|---|
| Enrich the generated specs and skills | `/sa:learn` |
| Take one ticket from grooming to verified code | `/sa:flow PROJ-123` |
| Plan and write unit tests for a module | `/sa:test-plan <module>`, then `/sa:test-run <module>` |
| Write the documentation the repository type needs | `/sa:docs` |
| Review architecture health | `/sa:analyze` |

The command syntax in other tools is listed in [AI coding tool support](PROVIDERS.md).

## What to read next

| Document | Contents |
|---|---|
| [Tool reference](TOOLS.md) | Every MCP tool, by tier, generated from the server |
| [AI coding tool support](PROVIDERS.md) | Files written per tool, per-tool limits and sources |
| [Architecture and decisions](ARCHITECTURE.md) | Components, rules enforced in code, and the decision records |
| [Benchmarks and evals](BENCHMARKS.md) | Indexing speed, token cost, retrieval quality, call-graph accuracy and how to reproduce each |
| [Releasing](RELEASING.md) | Release steps, PyPI publishing and site publishing |

The full README, the changelog and the roadmap are in the [repository](https://github.com/Abdulwahab26/SpecAnvil).

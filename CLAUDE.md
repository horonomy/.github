# CLAUDE.md — org-wide baseline (horonomy)

Canonical, shared context for Claude Code (and humans) across **all** repos in the
[`horonomy`](https://github.com/orgs/horonomy/repositories) org. Each repo may also
carry its own `.claude/CLAUDE.md` with repo-specific commands and gotchas; those
files **reference this baseline** instead of repeating it.

> **Note — this is a convention, not inheritance.** GitHub does not auto-apply a
> CLAUDE.md across repos. Claude Code reads the CLAUDE.md in the repo you are working
> in (plus your machine's global `~/.claude/CLAUDE.md`). This file is the single
> source of truth that per-repo files link to; keep org-universal facts here and
> repo-specific facts in each repo's `.claude/CLAUDE.md`.

## The company in one paragraph

Horonomy builds infrastructure products for autonomous software systems —
assembled, governed, and auditable. Software is becoming autonomous, but autonomy
without boundaries becomes drift; Horonomy builds the systems that make autonomy
explicit, governable, and safe to scale. The design ethos is boundary-aware,
composable rather than monolithic, and auditable from the start.

## Products & repos (which repo does what)

| Product / repo | Role |
|---|---|
| **AI Agent Assembly** | Governance platform for AI agents. Lives in its own org, [`ai-agent-assembly`](https://github.com/ai-agent-assembly), with its own org-baseline CLAUDE.md. |
| **ArcheWeave** | Future product — not yet public. Details to be confirmed. |
| **Harbinger** | Future product — not yet public. Details to be confirmed. |
| `official-website` | The `horonomy.dev` marketing site (Docusaurus + TypeScript). |
| `inner-document` | Internal documentation site (private). |
| `.github` | This repo — org profile (`profile/README.md`), community-health files, and this baseline. |

## Universal conventions (each repo's CONTRIBUTING.md is authoritative)

- **Commits:** `<emoji> (<scope>): <imperative summary>` (gitmoji.dev). One logical
  unit per commit; bisectable; utils/mocks/tests are separate preceding commits.
- **Branch:** `<release-or-phase>/<ticket>/<type>/<short_summary>` —
  e.g. `v0.1.0/HORO-14/feat/add_org_profile`. Types: feat/fix/refactor/test/docs/
  config/deps/remove/lint.
- **PR title:** `[<ticket>] <emoji> (<scope>): <summary>`; body follows the repo's PR
  template; ≥1 approval. **Never merge to base directly — PR only.**
- **Worktrees:** develop each ticket in a worktree off the latest default branch so
  the main checkout stays clean; remove the worktree after merge.

## Git remotes & default branches (these vary per repo — always detect)

- Run `git remote -v` and push to the remote pointing at `horonomy/<repo>`. A local
  `origin` may be a personal fork in some checkouts — **never assume `origin`**.
- **Default branch varies:** confirm with `git ls-remote --symref <remote> HEAD`.
  Most Horonomy web/docs repos use `main`.
- The org id is lowercase `horonomy` everywhere. Product orgs carry their own id
  (e.g. lowercase `ai-agent-assembly`).

## JIRA (project HORO)

Track work in the Horonomy Jira project (**HORO**). Hierarchy is Epic → Story →
Subtask (one Subtask ≈ one commit), with a `Verify …` subtask per Story. Set the
Component on every ticket to the GitHub repo it targets.

## Documentation conventions — document the WHY, not the WHAT

Comments and docstrings capture intent the code cannot: rationale, constraints,
invariants, non-obvious decisions. Restating what the code already says is noise that
rots out of sync — delete it.

- **Module / package** docs: role in the architecture + key invariants.
- **Public API** docs: the contract — behavior, errors, units, side effects, and any
  threading/async/`unsafe`/ordering constraints (especially the surprising ones).
  Use the language's idiom: rustdoc `//!`/`///`, Google-style Python docstrings,
  TSDoc on exports, godoc doc-comments starting with the identifier name.
- **Inline why-comments:** workarounds, perf-sensitive code, security rationale,
  dependency pins (explain *why* pinned). These are the highest-value comments.
- **Skip:** trivial helpers, getters, type-restating, per-variable docstrings.
- **Big architectural decisions → ADRs**, not scattered docstrings; link code to the
  ADR. Reference existing design specs rather than copying them.

> A new contributor (human or LLM) should read a module's header + a public item's
> doc and understand *why it is the way it is* without reverse-engineering it.

# Contributing to Horonom

Thank you for your interest in contributing! Horonom builds infrastructure products for autonomous software systems, and we welcome contributions of all sizes — bug reports, documentation fixes, new features, performance improvements, and more.

This guide describes the org-wide conventions that apply across all repositories under [horonomy](https://github.com/horonomy). For repo-specific setup (build commands, test runners, language toolchains), see the `CONTRIBUTING.md` in each individual repo — it takes precedence over this file where they differ.

> Some Horonom products live in their own GitHub organizations (for example, **AI Agent Assembly** at [github.com/ai-agent-assembly](https://github.com/ai-agent-assembly)) and carry their own contribution guides. Those product-level guides are authoritative for their repositories.

## Prerequisites

- A GitHub account
- Git installed locally
- Familiarity with the language and tooling of the repo you're contributing to
- The repo cloned locally (see each repo's README for setup steps)

## Branch naming

All feature and fix branches follow this four-part format:

```
<release-or-phase>/<ticket-number>/<type>/<short-summary>
```

- `<release-or-phase>` — milestone or sprint identifier (e.g., `v0.1.0`, `phase1`)
- `<ticket-number>` — Jira ticket reference (e.g., `HORO-14`)
- `<type>` — change category, one of:

  | Type | When to use |
  |---|---|
  | `feat` | New feature or capability |
  | `fix` | Bug fix |
  | `refactor` | Refactor with no behavior change |
  | `test` | Test-only change |
  | `docs` | Documentation change |
  | `config` | Configuration / CI change |
  | `deps` | Dependency upgrade |
  | `remove` | Deletion or removal |
  | `lint` | Lint or type-error fix |

- `<short-summary>` — 2–4 words in `snake_case`, max 30 characters

Example: `v0.1.0/HORO-14/feat/add_org_profile`

## Commit messages

Commits use [Gitmoji](https://gitmoji.dev/) format with a scope and imperative summary:

```
<emoji> (<scope>): <imperative summary>
```

Examples:

- `✨ (site): Add products section to the homepage`
- `🐛 (nav): Fix broken link to the security policy`
- `♻️ (hero): Extract the star field into its own component`

Keep each commit small and **bisectable** — one logical change per commit. If you need two sentences to describe a commit, split it into two.

### GitEmoji reference

| Emoji | Scope |
|---|---|
| `✨` | New feature |
| `🐛` | Bug fix |
| `♻️` | Refactor |
| `✅` | Tests |
| `📝` | Documentation |
| `🔧` | Configuration / CI |
| `⬆️` | Dependency upgrade |
| `🗑️` | Removal |
| `🚨` | Lint / type-error fix |
| `🎉` | Initial commit |

## Pull Requests

### Title

```
[<ticket>] <emoji> (<scope>): <imperative summary>
```

Example: `[HORO-14] 🔧 (org): Bootstrap org-level community health files`

### Description

Each repo provides a `.github/pull_request_template.md`. Fill it out completely; at minimum the description must include:

- **What changed** — one short paragraph
- **Why** — motivation, context, ticket link
- **How to verify** — manual steps or test reference
- **Related issues** — ticket and any related GitHub issues

### Base branch

PRs target the repository's default branch (most Horonom repos use `main`). Confirm the default branch before opening a PR.

### Scope

Keep PRs focused. One concern per PR — don't bundle unrelated changes. If a single ticket needs more than ~500 lines of diff, split it into a sequence of stacked PRs.

## Code review

- At least **one approval** is required before merge.
- Address every reviewer comment before requesting re-review.
- Don't force-push during an active review (only allowed when rebasing onto the latest base branch — never to rewrite review history).
- CI must be green before merge. Don't bypass with `--no-verify` or by disabling checks.
- The reviewer or assignee picks the merge strategy (typically squash or rebase). The repo's default reflects the team's preference.

## New-repository checklist

Starting a new Horonom repository (HORO-290)? Beyond the usual scaffolding
(license, `.gitignore`, CI), check these rebrand-era conventions before the
first PR merges:

- **Company/product identity** — the company is **Horonom**, not Horonomy.
  `github.com/horonomy` is a permanent technical namespace exception (the
  exact-match slug is squatted, see ADR-0001) — do not "fix" it to
  `horonom` anywhere in the new repo.
- **Canonical domains** — corporate site is `https://horonom.com`; the
  public product family lives under `*.horo.run` (ADR-0002) unless the
  product already has a standalone canonical domain for a documented reason
  (e.g. `agent-assembly.com`). `horonom.dev` is reserved for a future
  Developer Platform and stays out of a new product's canonical URL set
  unless that platform has actually been activated (see policy-0001 in
  `horonomy/internal-docs`).
- **Product Registry** — if the new repository is a public-facing Horonom
  product, add one entry to the canonical
  [Product Registry](https://github.com/horonomy/official-website/blob/main/src/data/productRegistry.ts)
  (HORO-282) rather than hand-writing product facts (name, category,
  maturity, canonical URL) anywhere else — `horonom.com`'s System Map and
  `horo.run`'s Atlas both render straight from it, and a second
  hand-maintained copy of the same facts is exactly the drift this
  registry exists to prevent.
- **Maturity labels** — use the Registry's controlled vocabulary
  (`experimental` / `beta` / `release_candidate` / `available`). Never mark
  a product `available` unless it is genuinely generally available.
- **Machine identifiers** — package names, CLIs, and API identifiers are
  not renamed for cosmetic consistency alone; only rename one for a real
  compatibility reason.

## Developer Certificate of Origin

By contributing to Horonom, you certify that:

1. The contribution was created in whole or in part by you, **or**
2. The contribution is based on work that is licensed under an appropriate open-source license, **or**
3. The contribution was provided directly to you by someone who certified (1) or (2).

We do not currently require explicit commit sign-off (`git commit -s`), but reserve the right to add DCO bot enforcement in the future. If we do, we'll announce it on the relevant repo's Discussions and update this section.

## License

By contributing, you agree that your contributions will be licensed under the same license as the repository you're contributing to. See the `LICENSE` file in each repo for the applicable terms.

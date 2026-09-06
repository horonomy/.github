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

Horonom builds infrastructure products for autonomous software systems —
assembled, governed, and auditable. Software is becoming autonomous, but autonomy
without boundaries becomes drift; Horonom builds the systems that make autonomy
explicit, governable, and safe to scale. The design ethos is boundary-aware,
composable rather than monolithic, and auditable from the start.

## Products & repos (which repo does what)

The current public product catalog is **not** hand-maintained here — see
[`metadata/company.yaml`](metadata/company.yaml) (machine-readable) or
[`profile/README.md`](profile/README.md) (the org profile) for the
authoritative, up-to-date list. Duplicating it here would only create a
second copy that can drift, which is exactly the problem those files exist
to prevent. Non-catalog Horonom repos in this org: `official-website` (the
`horonom.com` marketing site), `internal-docs` (private internal docs),
and `.github` (this repo — org profile, community-health files, and this
baseline).

## Company-wide governance — precedence and detailed rules

Governance applies **Company → Product → Repository**: a narrower context
may add to or strengthen a rule here, never weaken a non-waivable one. The
detailed, testable rule text (merge strategy, security, testing/review,
Jira delivery, releases, autonomous-execution) lives in
[`governance/`](governance/) — start at
[`governance/README.md`](governance/README.md). Durable cross-repo
architecture decisions are recorded as ADRs in
[`horonomy/internal-docs`](https://github.com/horonomy/internal-docs/tree/main/docs/engineering).

## Domain and analytics convention (non-waivable)

**Horonomy lives on `.com`. Horonomy products run on `.run`.**
`<product>.horonom.com` is the canonical public product/marketing site;
`docs.<product>.horonom.com` is the canonical docs surface once dedicated
docs exist; `app/api/ingest.<product>.horo.run` are executable/runtime
boundaries only, provisioned only when the real service exists —
`<product>.horo.run` is not the default marketing hostname after
migration. **Exception:** the bare root `horo.run` is the deliberate
Horonom Product Atlas / product-directory entry point, using the Horonom
**company** GA4 property — never a product property — see ADR-0008
(HORO-594/595). GA4 is allowed only on approved public marketing/docs surfaces
and never carries prompt/agent/repo/tenant/PII/credential/authenticated-
SaaS content; authenticated product telemetry is a separate privacy
domain. Full constitution, security checklist, and precedence:
[`governance/engineering/domains-analytics.md`](governance/engineering/domains-analytics.md)
→ [ADR-0006](https://github.com/horonomy/internal-docs/blob/main/docs/engineering/adr-0006-horonom-domain-surface-and-analytics-constitution.md)
(HORO-566). Product-specific domain/DNS/analytics implementation is
product-owned, not centralized here.

## Documentation analytics convention (non-waivable)

A **Documentation** GA4 stream is a separate boundary from marketing
analytics and from authenticated product telemetry — never assume they
share a stream, property, or event vocabulary, and never assume a
Documentation stream URL proves the corresponding docs DNS/site exists.
Common docs event vocabulary (`page_view` plus only real interactions
like `docs_search`, `code_copy`, `install_command_copy`,
`quickstart_click`, `github_click`, `docs_feedback`, etc.) and the
forbidden-parameter list (no raw search text, copied code, prompts,
repo/tenant/user identity, credentials, authenticated payloads, or
Product Truth/security content) are fixed centrally so no product session
invents its own. A Measurement ID in a bundle, `gtag.js` 200, or a
synthetic `g/collect` 204 is never sufficient proof of working analytics —
required evidence is a real production browser emitting the event via
real navigation/interaction. Full constitution, event vocabulary,
parameter policy, and verification standard:
[`governance/engineering/docs-analytics.md`](governance/engineering/docs-analytics.md)
→ [ADR-0007](https://github.com/horonomy/internal-docs/blob/main/docs/engineering/adr-0007-documentation-analytics-constitution.md)
(HORO-587), extending
[ADR-0006](https://github.com/horonomy/internal-docs/blob/main/docs/engineering/adr-0006-horonom-domain-surface-and-analytics-constitution.md)
(HORO-566). Product-specific docs-analytics implementation is
product-owned, not centralized here.

## Universal conventions

Commit/branch/PR format and worktree workflow are defined once, in
[`CONTRIBUTING.md`](CONTRIBUTING.md) and
[`governance/engineering/git-pr-merge.md`](governance/engineering/git-pr-merge.md)
— read those rather than this file for the exact format strings, so there
is only one place to keep them current. Each repo's own `CONTRIBUTING.md`
is authoritative for anything it adds on top.

## Git remotes & default branches (these vary per repo — always detect)

- Run `git remote -v` and push to the remote pointing at `horonomy/<repo>`. A local
  `origin` may be a personal fork in some checkouts — **never assume `origin`**.
- **Default branch varies:** confirm with `git ls-remote --symref <remote> HEAD`.
  Most Horonom web/docs repos use `main`.
- The org id is lowercase `horonomy` everywhere. Product orgs carry their own id
  (e.g. lowercase `ai-agent-assembly`).

## JIRA (project HORO)

Track work in the Horonom Jira project (**HORO**). Hierarchy is Epic → Story →
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
